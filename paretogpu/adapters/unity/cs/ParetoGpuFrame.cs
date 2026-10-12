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
// The settings the frame is drawn with (plan item K1.12) go next to the events: build target, quality level, the Game
// view size and the active render pipeline asset (QualitySettings.renderPipeline, else the Graphics settings' one) as
// its SerializedObject shows it: every visible field, and the ScriptableObjects it refers to (renderers and their
// features in URP) the same way, two levels down. Any SRP is written alike; a pipeline that takes its settings from
// elsewhere at run time shows its asset's values, so Python compares the asset's render scale with the frame's.
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
        if (!EditorApplication.isPlaying)
            return ParetoGpuJob.Fail("not_playing", "the editor is not in Play Mode: an Edit Mode frame is not the game's");

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
                                                   "the Game view visible and retry");
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
          .Append(", \"quality_level\": ").Append(QualitySettings.GetQualityLevel())
          .Append(", \"build_target\": ").Append(Str(EditorUserBuildSettings.activeBuildTarget.ToString()))
          .Append(", \"screen\": ").Append(GameViewSize())
          .Append(",\n \"pipeline\": ").Append(PipelineJson())
          .Append(",\n \"play_mode\": ").Append(EditorApplication.isPlaying ? "true" : "false")
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
        sb.Append(", \"params\": ").Append(ParamsJson());
        sb.Append(", \"data\": ").Append(Value(data, 0)).Append("}");
        return sb.ToString();
    }

    // the size the Game view renders at (its resolution drop-down), else the Screen of the player loop
    static string GameViewSize()
    {
        var view = typeof(EditorWindow).Assembly.GetType("UnityEditor.PlayModeView");
        var size = Safe(() => view?.GetMethod("GetMainPlayModeViewTargetSize", S, null, Type.EmptyTypes, null)
            ?.Invoke(null, null)) is Vector2 v && v.x > 0 ? v : new Vector2(Screen.width, Screen.height);
        return $"[{(int)size.x}, {(int)size.y}]";
    }

    static string PipelineJson()
    {
        var rp = QualitySettings.renderPipeline != null ? QualitySettings.renderPipeline
            : UnityEngine.Rendering.GraphicsSettings.defaultRenderPipeline;
        if (rp == null) return "null";
        var seen = new HashSet<UnityEngine.Object>();
        return AssetJson(rp, 0, seen);
    }

    const int AssetDepth = 2, AssetFields = 400, AssetRefs = 32;

    // {"name", "class", "fields": {property path: value}, "refs": [the ScriptableObjects it refers to, alike]}
    static string AssetJson(ScriptableObject o, int depth, HashSet<UnityEngine.Object> seen)
    {
        seen.Add(o);
        var fields = new List<string>();
        var refs = new List<ScriptableObject>();
        var so = new SerializedObject(o);
        var p = so.GetIterator();
        var enter = true;
        while (p.NextVisible(enter) && fields.Count < AssetFields)
        {
            enter = p.propertyType != SerializedPropertyType.String;  // a string's characters are not fields
            if (p.propertyPath == "m_Script") continue;
            var v = PropertyJson(p);
            if (v != null) fields.Add(Str(p.propertyPath) + ": " + v);
            if (p.propertyType == SerializedPropertyType.ObjectReference && p.objectReferenceValue is ScriptableObject r
                && r != null && !seen.Contains(r) && !refs.Contains(r) && seen.Count + refs.Count < AssetRefs)
                refs.Add(r);
        }
        var nested = depth < AssetDepth ? refs.Where(r => !seen.Contains(r)).Select(r => AssetJson(r, depth + 1, seen)).ToList()
            : new List<string>();
        return $"{{\"name\": {Str(o.name)}, \"class\": {Str(o.GetType().Name)}, " +
               $"\"fields\": {{{string.Join(", ", fields)}}}, \"refs\": [{string.Join(", ", nested)}]}}";
    }

    static string PropertyJson(SerializedProperty p)
    {
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        switch (p.propertyType)
        {
            case SerializedPropertyType.Integer:
            case SerializedPropertyType.ArraySize:
            case SerializedPropertyType.LayerMask: return p.longValue.ToString(inv);
            case SerializedPropertyType.Boolean: return p.boolValue ? "true" : "false";
            case SerializedPropertyType.Float: return Value(p.floatValue, 0);
            case SerializedPropertyType.String: return Str(p.stringValue);
            case SerializedPropertyType.Enum:
                var names = p.enumDisplayNames;
                return Str(p.enumValueIndex >= 0 && p.enumValueIndex < names.Length ? names[p.enumValueIndex] : p.intValue.ToString(inv));
            case SerializedPropertyType.ObjectReference:
                return p.objectReferenceValue == null ? "null" : Str(p.objectReferenceValue.name);
            case SerializedPropertyType.Vector2: return Str(p.vector2Value.ToString());
            case SerializedPropertyType.Vector3: return Str(p.vector3Value.ToString());
            case SerializedPropertyType.Vector4: return Str(p.vector4Value.ToString());
            case SerializedPropertyType.Vector2Int: return Str(p.vector2IntValue.ToString());
            case SerializedPropertyType.Color: return Str(p.colorValue.ToString());
            default: return null;
        }
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

    // {name: value} of the event's float, int and vector shader parameters (m_ShaderInfo: the values the draw used; a
    // vector is [x, y, z, w]); a name met again (an array) gets a list of its values. Python reads the bounds of
    // dynamic loops from them (core/loops.py). Matrices, textures and buffers are left out.
    static string ParamsJson()
    {
        var info = dataType.GetField("m_ShaderInfo", I)?.GetValue(data);
        if (info == null) return "{}";
        var values = new Dictionary<string, List<string>>();
        foreach (var list in new[] { "m_Floats", "m_Ints", "m_Vectors" })
        {
            if (!(info.GetType().GetField(list, I)?.GetValue(info) is Array arr)) continue;
            foreach (var item in arr)
            {
                var t = item.GetType();
                var name = t.GetField("m_Name", I)?.GetValue(item) as string;
                var v = t.GetField("m_Value", I)?.GetValue(item);
                if (string.IsNullOrEmpty(name) || v == null) continue;
                var json = v is Vector4 x ? $"[{Value(x.x, 0)}, {Value(x.y, 0)}, {Value(x.z, 0)}, {Value(x.w, 0)}]" : Value(v, 0);
                if (!values.TryGetValue(name, out var vs)) values[name] = vs = new List<string>();
                if (vs.Count < 64) vs.Add(json);
            }
        }
        return "{" + string.Join(", ", values.Select(kv => Str(kv.Key) + ": " +
            (kv.Value.Count == 1 ? kv.Value[0] : "[" + string.Join(", ", kv.Value) + "]"))) + "}";
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
