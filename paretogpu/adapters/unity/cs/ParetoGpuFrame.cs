// Frame Debugger snapshot: job "frame", config {"max_events": 0} -> <out>/frame.json. Start() returns "started"; Tick
// (EditorApplication.update) waits for a stable event count, replays event by event (limit = i + 1) and dumps every
// FrameDebuggerEventData field by reflection (internal API); Python picks what it needs. Driven via
// FrameDebuggerUtility as its window does: enable = pause Play Mode + SetEnabled(true, connectedProfiler); new limit =
// limit + scene repaint; disable = SetEnabled(false, GetRemotePlayerGUID()). The pipeline asset is dumped via
// SerializedObject two levels deep; a pipeline that sets values at run time shows its asset's, so Python compares the
// asset's render scale with the frame's.

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
    static int maxEvents, state, index, count, waited, stable, lastCount, nudged;
    static bool wasEnabled, wasPaused, restore;
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
        // enabling pauses Play Mode and the replay moves the limit: Stop() restores both
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
            if (state == 0)  // wait for a stable event count
            {
                // an unfocused editor does not render the Game view by itself
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
            if (state == 1)
            {
                if (restore)  // second half of a nudge (below)
                {
                    restore = false;
                    SetLimit(index + 1);
                    return;
                }
                bool ok = (bool)Call("GetFrameEventData", index, data);
                int got = (int)dataType.GetField("m_FrameEventIndex", I).GetValue(data);
                if (!ok || got != index)
                {
                    if (++waited % 50 == 0) Progress($"waiting for event {index}: {waited} ticks");
                    var wait = EditorApplication.timeSinceStartup - asked;
                    if (wait > 60)
                        throw new Exception($"no data for event {index} of {count} in 60 s");
                    // an unfocused editor may drop a replay: every 5 s the limit moves away for a tick and back
                    EditorApplication.QueuePlayerLoopUpdate();
                    if (wait > 5 * (nudged + 1))
                    {
                        nudged++;
                        restore = true;
                        SetLimit(index > 0 ? index : index + 2);
                    }
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

    static void Request(int i)
    {
        asked = EditorApplication.timeSinceStartup;
        nudged = 0;
        restore = false;
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
          .Append(", \"android_quality\": [").Append(string.Join(", ", QualitySettings.GetActiveQualityLevelsForPlatform("Android").Select(i => Str(QualitySettings.names[i])))).Append("]")
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

    static string GameViewSize()
    {
        var view = typeof(EditorWindow).Assembly.GetType("UnityEditor.PlayModeView");
        var size = Safe(() => view?.GetMethod("GetMainPlayModeViewTargetSize", S, null, Type.EmptyTypes, null)
            ?.Invoke(null, null)) is Vector2 v && v.x > 0 ? v : new Vector2(Screen.width, Screen.height);
        return $"[{(int)size.x}, {(int)size.y}]";
    }

    static string PipelineJson()
    {
        var (rp, path, hash) = ParetoGpuJob.Pipeline();
        if (rp == null) return "null";
        var seen = new HashSet<UnityEngine.Object>();
        return "{\"path\": " + Str(path) + ", \"hash\": " + Str(hash) + ", " + AssetJson(rp, 0, seen).Substring(1);
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
        var active = so.FindProperty("m_Active");
        if (active != null && active.propertyType == SerializedPropertyType.Boolean)
            fields.Add(Str("m_Active") + ": " + (active.boolValue ? "true" : "false"));
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

    // m_MeshInstanceIDs (Unity 6.3) or m_MeshEntityIds (6.4+)
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

    // {name: value, or [values] for an array} of the draw's float/int/vector params (vector = [x, y, z, w]);
    // core/loops.py reads dynamic loop bounds from them. Matrices, textures and buffers are left out.
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

    // bloat the snapshot; ParamsJson writes the needed ones
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

    // as the Frame Debugger window does (RepaintAllNeededThings): the replay shows on the next repaint
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
