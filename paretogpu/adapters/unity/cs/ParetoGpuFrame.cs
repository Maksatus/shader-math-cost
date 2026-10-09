// Frame snapshot through the Frame Debugger (plan items K1.1, K1.2).
//
// Used by `python -m paretogpu frame`:
//   adapters/unity/bridge.py call(..., "ParetoGpuFrame.cs", "Start", <out>, "frame", ...)
// Start() returns at once; the snapshot runs on EditorApplication.update: enable the Frame Debugger (if it is
// off), wait for the captured frame, then replay it event by event (limit = i + 1) and read each event's data.
// The result goes to <out>/frame.json, progress to <out>/frame.progress; the Frame Debugger is restored after.
//
// The Frame Debugger API is internal (UnityEditorInternal.FrameDebuggerInternal), so everything goes through
// reflection and every field of FrameDebuggerEventData is written as is: Python picks what it needs.
//
// Pixels and vertices come from RenderDoc (adapters/renderdoc), not from here: the replay only reads event data.
//
// The Frame Debugger is driven through FrameDebuggerUtility alone, as its window does (EnableFrameDebugger:
// pause Play Mode, SetEnabled(true, ProfilerDriver.connectedProfiler); a new limit: limit = n and a scene repaint;
// DisableFrameDebugger: SetEnabled(false, GetRemotePlayerGUID())); no window is opened.
//
// config: {"max_events": 0} (and the job's "out", "name": cs/ParetoGpuJob.cs)

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class ParetoGpuFrame
{
    [Serializable]
    class Config
    {
        public int max_events = 0;
    }

    const BindingFlags S = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    const BindingFlags I = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    static Type util, dataType;
    static int maxEvents, state, index, count, waited, stable, lastCount;
    static bool wasEnabled, wasPaused;
    static int wasLimit;
    static string token;
    // run_script loads every run as a new assembly: a newer run takes the token and older Tick handlers quit
    const string TokenKey = "paretogpu.frame.token";
    static object data;
    static List<string> events;
    static double started, asked;

    public static string Start(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(ParetoGpuJob.Open(configPath));
        maxEvents = cfg.max_events;

        util =AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerUtility"))
            .FirstOrDefault(t => t != null);
        dataType = util?.Assembly.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerEventData");
        if (util == null || dataType == null)
            return ParetoGpuJob.Fail("error", "FrameDebuggerUtility not found in this Unity version");
        if (!(bool)Prop("locallySupported"))
            return ParetoGpuJob.Fail("error", "Frame Debugger is not supported for the current graphics API");

        wasEnabled = (int)Prop("count") > 0;  // a disabled Frame Debugger has no events
        // enabling the Frame Debugger pauses Play Mode and the replay moves the limit: both are set back in Stop()
        wasPaused = EditorApplication.isPaused;
        wasLimit = wasEnabled && Safe(() => Prop("limit")) is int lim ? lim : 0;
        ParetoGpuJob.KeepRenderingInBackground();
        if (!wasEnabled)
        {
            if (EditorApplication.isPlaying && !EditorApplication.isPaused) EditorApplication.isPaused = true;
            Call("SetEnabled", true, UnityEditorInternal.ProfilerDriver.connectedProfiler);
        }
        token = Guid.NewGuid().ToString();
        SessionState.SetString(TokenKey, token);
        data = Activator.CreateInstance(dataType);
        events = new List<string>();
        state = 0; waited = 0; stable = 0; lastCount = -1; index = 0;
        started = EditorApplication.timeSinceStartup;
        EditorApplication.update -= Tick;
        EditorApplication.update += Tick;
        Progress("capturing");
        return "started";
    }

    static void Tick()
    {
        if (SessionState.GetString(TokenKey, "") != token)
        {
            EditorApplication.update -= Tick;
            return;
        }
        try
        {
            UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            if (EditorApplication.timeSinceStartup - started > 1800)
                throw new Exception("timeout");
            if (state == 0)  // wait until the captured frame is there and its event count is stable
            {
                // an unfocused editor in Edit Mode does not render the Game view by itself
                EditorApplication.QueuePlayerLoopUpdate();
                SceneRepaintDirty();
                if (EditorApplication.timeSinceStartup - started > 60)
                    throw new ParetoGpuJob.Failure("no_frame", "the Frame Debugger did not capture a frame in 60 s: make " +
                                                   "the Game view visible (or enter Play Mode) and retry");
                int c = (int)Prop("count");
                stable = c > 0 && c == lastCount ? stable + 1 : 0;
                lastCount = c;
                if (stable < 5) return;
                count = maxEvents > 0 ? Math.Min(c, maxEvents) : c;
                state = 1;
                Request(0);
                return;
            }
            if (state == 1)  // replay up to event `index` and read its data
            {
                bool ok = (bool)Call("GetFrameEventData", index, data);
                int got = (int)dataType.GetField("m_FrameEventIndex", I).GetValue(data);
                if (!ok || got != index)
                {
                    if (++waited % 50 == 0) Progress($"waiting for event {index}: {waited} ticks");
                    if (EditorApplication.timeSinceStartup - asked > 30)
                        throw new Exception($"no data for event {index} of {count} in 30 s");
                    return;
                }
                waited = 0;
                events.Add(EventJson(index));
                index++;
                if (index % 25 == 0) Progress($"events {index}/{count}");
                if (index >= count) { Finish(); return; }
                Request(index);
            }
        }
        catch (Exception e)
        {
            Stop();
            ParetoGpuJob.Fail(e);
        }
    }

    // replay up to event i (limit = i + 1); Tick reads its data once the replay is there
    static void Request(int i)
    {
        asked = EditorApplication.timeSinceStartup;
        SetLimit(i + 1);
    }

    static void Finish()
    {
        var evs = (Array)Call("GetFrameEvents");
        var sb = new StringBuilder();
        sb.Append("{\"unity\": ").Append(Str(Application.unityVersion))
          .Append(", \"project\": ").Append(Str(Path.GetDirectoryName(Application.dataPath)))
          .Append(", \"graphics_api\": ").Append(Str(SystemInfo.graphicsDeviceType.ToString()))
          .Append(", \"quality\": ").Append(Str(QualitySettings.names[QualitySettings.GetQualityLevel()]))
          .Append(", \"play_mode\": ").Append(EditorApplication.isPlaying ? "true" : "false")
          .Append(", \"total_events\": ").Append(evs?.Length ?? count)
          .Append(", \"seconds\": ").Append((EditorApplication.timeSinceStartup - started).ToString("0.0", System.Globalization.CultureInfo.InvariantCulture))
          .Append(",\n \"events\": [\n").Append(string.Join(",\n", events)).Append("\n]}\n");
        Stop();
        ParetoGpuJob.Done(sb.ToString());
    }

    static void Stop()
    {
        EditorApplication.update -= Tick;
        if (SessionState.GetString(TokenKey, "") == token)
            SessionState.EraseString(TokenKey);
        try
        {
            if (wasEnabled) SetLimit(wasLimit > 0 ? wasLimit : (int)Prop("count"));
            else
            {
                Call("SetEnabled", false, (int)Call("GetRemotePlayerGUID"));
                SceneRepaintDirty();
            }
        }
        catch { }
        if (EditorApplication.isPlaying && EditorApplication.isPaused != wasPaused)
            EditorApplication.isPaused = wasPaused;
        ParetoGpuJob.RestoreBackground();
    }

    static string EventJson(int i)
    {
        var evs = (Array)Call("GetFrameEvents");
        var ev = evs != null && i < evs.Length ? evs.GetValue(i) : null;
        var sb = new StringBuilder("{\"index\": ").Append(i);
        if (ev != null)
        {
            var t = ev.GetType();
            sb.Append(", \"type\": ").Append(Str(t.GetField("m_Type", I)?.GetValue(ev)?.ToString()));
            sb.Append(", \"object\": ").Append(Value(t.GetField("m_Obj", I)?.GetValue(ev), 0));
        }
        sb.Append(", \"name\": ").Append(Str((string)Call("GetFrameEventInfoName", i)));
        sb.Append(", \"meshes\": ").Append(MeshNames());
        sb.Append(", \"data\": ").Append(Value(data, 0)).Append("}");
        return sb.ToString();
    }

    // JSON of any value: primitives, strings, enums, arrays, Unity objects (name + id), structs/classes (fields)
    static string Value(object v, int depth)
    {
        switch (v)
        {
            case null: return "null";
            case string s: return Str(s);
            case bool b: return b ? "true" : "false";
            case Enum e: return Str(e.ToString());
            case float f: return float.IsFinite(f) ? f.ToString("R", System.Globalization.CultureInfo.InvariantCulture) : "null";
            case double d: return double.IsFinite(d) ? d.ToString("R", System.Globalization.CultureInfo.InvariantCulture) : "null";
            case IntPtr _: return "null";
            case UnityEngine.Object o:
                if (o == null) return "null";
                var extra = o is Texture tex ? $", \"width\": {tex.width}, \"height\": {tex.height}" : "";
                return $"{{\"name\": {Str(o.name)}, \"id\": {o.GetInstanceID()}, \"class\": {Str(o.GetType().Name)}{extra}}}";
        }
        var type = v.GetType();
        if (type.IsPrimitive)
            return Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture);
        if (depth > 3)
            return Str(v.ToString());
        if (v is IEnumerable list)
        {
            var items = new List<string>();
            foreach (var x in list)
            {
                if (items.Count >= 64) { items.Add(Str("...")); break; }
                items.Add(Value(x, depth + 1));
            }
            return "[" + string.Join(", ", items) + "]";
        }
        var fields = type.GetFields(I).Where(f => !f.Name.StartsWith("<") && !Heavy.Contains(f.Name));
        return "{" + string.Join(", ", fields.Select(f => Str(f.Name) + ": " + Value(Safe(() => f.GetValue(v)), depth + 1))) + "}";
    }

    // names of the meshes of a (batched) draw: m_MeshInstanceIDs (6.3) or m_MeshEntityIds (6.4+)
    static string MeshNames()
    {
        var names = new List<string>();
        foreach (var fname in new[] { "m_MeshInstanceIDs", "m_MeshEntityIds" })
        {
            if (!(dataType.GetField(fname, I)?.GetValue(data) is IEnumerable ids)) continue;
            foreach (var id in ids)
            {
                UnityEngine.Object o = null;
                try
                {
                    o = id is int n ? EditorUtility.InstanceIDToObject(n)
                        : typeof(EditorUtility).GetMethods(S).FirstOrDefault(m => m.Name == "EntityIdToObject" && m.GetParameters().Length == 1)
                            ?.Invoke(null, new[] { id }) as UnityEngine.Object;
                }
                catch { }
                if (o != null) names.Add(o.name);
                if (names.Count >= 64) break;
            }
        }
        return "[" + string.Join(", ", names.Select(Str)) + "]";
    }

    // shader property values (matrices, vectors, floats, buffers) bloat the snapshot and are not needed
    static readonly HashSet<string> Heavy = new HashSet<string> { "m_Floats", "m_Ints", "m_Vectors", "m_Matrices", "m_Buffers", "m_CBuffers" };

    static object Safe(Func<object> f)
    {
        try { return f(); } catch { return null; }
    }

    static void SetLimit(int n)
    {
        util.GetProperty("limit", S).SetValue(null, n);
        SceneRepaintDirty();
        UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
    }

    // what the Frame Debugger window does after a change (RepaintAllNeededThings): the replay shows on the next repaint
    static void SceneRepaintDirty() =>
        typeof(EditorApplication).GetMethod("SetSceneRepaintDirty", S)?.Invoke(null, null);

    static object Prop(string name) => util.GetProperty(name, S).GetValue(null);

    static object Call(string name, params object[] args)
    {
        var m = util.GetMethods(S).First(x => x.Name == name && x.GetParameters().Length == args.Length);
        return m.Invoke(null, args);
    }

    static void Progress(string s) => ParetoGpuJob.Progress(s);

    static string Str(string v) => ParetoGpuJob.Str(v);
}
