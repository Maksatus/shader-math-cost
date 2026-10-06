// Frame snapshot through the Frame Debugger (plan items K1.1, K1.2).
//
// Used by `python -m shaderopt frame`:
//   unity command run_script --file ShaderoptFrame.cs --entry ShaderoptFrame.Start --args '["<config.json>"]'
// Start() returns at once; the snapshot runs on EditorApplication.update: enable the Frame Debugger (if it is
// off), wait for the captured frame, then replay it event by event (limit = i + 1) and read each event's data.
// The result goes to <out>/frame.json, progress to <out>/frame.progress; the Frame Debugger is restored after.
//
// The Frame Debugger API is internal (UnityEditorInternal.FrameDebuggerInternal), so everything goes through
// reflection and every field of FrameDebuggerEventData is written as is: Python picks what it needs.
//
// Pixels of an event: the Frame Debugger keeps a copy of the event's render target; it is compared on the GPU
// with the copy kept after the previous event that used a target of the same name. Every replay is a new frame
// (camera jitter moves the image by a sub-pixel offset, shading noise changes), so a pixel counts as changed only
// if its new value leaves the 3x3 neighbourhood range of the old one and enough of its neighbours changed too.
// Known misses: low-contrast blended draws (overlays, faint transparents), overlap inside one draw, clip/discard.
//
// config.json: {"out": "<absolute folder>", "max_events": 0, "pixels": true, "eps": 0.02,
//               "neighborhood": true, "min_neighbors": 3, "dump_masks": [event indices to save as PNG]}

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class ShaderoptFrame
{
    [Serializable]
    class Config
    {
        public string @out = "";
        public int max_events = 0;
        public bool pixels = true;
        public int[] dump_masks = new int[0];
        public float eps = 0.02f;  // relative tolerance of a "changed" pixel
        public bool neighborhood = true;  // ignore sub-pixel shifts between replays (camera jitter)
        public int min_neighbors = 3;     // a changed pixel needs this many changed pixels in its 3x3 (itself included); 1 = off  // debug: save the changed-pixel mask of these events as PNG
    }

    // pixel diff shader, compiled in memory: pass 0 marks changed pixels, pass 1 drops isolated ones
    const string DiffShader = @"Shader ""Hidden/ShaderoptDiff"" {
SubShader { Pass { ZTest Always ZWrite Off Cull Off
CGPROGRAM
#pragma vertex vert_img
#pragma fragment frag
#pragma target 4.5
#include ""UnityCG.cginc""
Texture2D _Cur;
Texture2D _Prev;
float _Eps;
float _Neighborhood;
float frag(v2f_img i) : SV_Target {
    int3 p = int3(i.pos.xy, 0);
    float4 a = _Cur.Load(p), b = _Prev.Load(p);
    if (_Neighborhood < 0.5) {
        float4 d = abs(a - b) - _Eps * max(max(abs(a), abs(b)), 1e-3);
        return any(d > 1e-6) ? 1 : 0;
    }
    // every replay is a new frame (camera jitter moves the image by a sub-pixel offset): a pixel counts as
    // changed by the event only if its new value is outside the 3x3 neighbourhood range of the previous one
    float4 lo = b, hi = b;
    [unroll] for (int y = -1; y <= 1; y++)
    [unroll] for (int x = -1; x <= 1; x++) {
        float4 n = _Prev.Load(p + int3(x, y, 0));
        lo = min(lo, n); hi = max(hi, n);
    }
    float4 tol = _Eps * max(abs(hi), 1e-3) + 1e-6;
    return any(a < lo - tol || a > hi + tol) ? 1 : 0;
}
ENDCG
}
Pass { ZTest Always ZWrite Off Cull Off
CGPROGRAM
#pragma vertex vert_img
#pragma fragment frag
#pragma target 4.5
#include ""UnityCG.cginc""
Texture2D _Mask;
float _MinNeighbors;
// drop isolated changed pixels (shading noise between replays): keep a pixel if enough of its 3x3 changed
float frag(v2f_img i) : SV_Target {
    int3 p = int3(i.pos.xy, 0);
    if (_Mask.Load(p).r < 0.5) return 0;
    float n = 0;
    [unroll] for (int y = -1; y <= 1; y++)
    [unroll] for (int x = -1; x <= 1; x++)
        n += _Mask.Load(p + int3(x, y, 0)).r > 0.5 ? 1 : 0;
    return n >= _MinNeighbors ? 1 : 0;
}
ENDCG
} }
}";

    static bool pixels;
    static HashSet<int> dumpMasks;
    static bool neighborhood;
    static int minNeighbors;
    static RenderTexture mask2;
    static float eps;
    static int pending = -1;          // event whose replay we wait for
    static string pendingPixels;      // its pixels, taken at the end of the replayed rendering
    static Material diffMat;
    static Dictionary<string, RenderTexture> prev;   // by render target name: the replay reallocates textures
    static Dictionary<(int, int), RenderTexture> masks;

    const BindingFlags S = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    const BindingFlags I = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    static Type util, dataType;
    static string outDir;
    static int maxEvents, state, index, count, waited, waitedPixels, stable, lastCount;
    static bool wasEnabled, openedWindow;
    static Type winType;
    static UnityEngine.Object window;
    static string token;
    // run_script loads every run as a new assembly: a newer run takes the token and older Tick handlers quit
    const string TokenKey = "shaderopt.frame.token";
    static object data;
    static List<string> events;
    static double started;

    public static string Start(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(File.ReadAllText(configPath));
        outDir = cfg.@out;
        maxEvents = cfg.max_events;
        Directory.CreateDirectory(outDir);
        foreach (var f in new[] { "frame.json", "frame.progress", "frame.error" })
            if (File.Exists(Path.Combine(outDir, f))) File.Delete(Path.Combine(outDir, f));

        var asm = typeof(UnityEditor.EditorWindow).Assembly;
        util = AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerUtility"))
            .FirstOrDefault(t => t != null);
        dataType = util?.Assembly.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerEventData");
        if (util == null || dataType == null)
            return Fail("FrameDebuggerUtility not found in this Unity version");
        if (!(bool)Prop("locallySupported"))
            return Fail("Frame Debugger is not supported for the current graphics API");

        // drive the Frame Debugger window: it finds the Game view and repaints it on enable / limit change
        winType = asm.GetType("UnityEditor.FrameDebuggerWindow");
        window = Resources.FindObjectsOfTypeAll(winType).FirstOrDefault();
        openedWindow = window == null;
        if (openedWindow)
        {
            winType.GetMethod("OpenWindow", S)?.Invoke(null, null);
            window = Resources.FindObjectsOfTypeAll(winType).FirstOrDefault();
        }
        if (window == null)
            return Fail("cannot open the Frame Debugger window");
        wasEnabled = (int)Prop("count") > 0;  // a disabled Frame Debugger has no events
        if (!wasEnabled)
            winType.GetMethod("EnableFrameDebugger", I).Invoke(window, null);
        token = Guid.NewGuid().ToString();
        SessionState.SetString(TokenKey, token);
        pixels = cfg.pixels;
        eps = cfg.eps;
        neighborhood = cfg.neighborhood;
        minNeighbors = cfg.min_neighbors;
        dumpMasks = new HashSet<int>(cfg.dump_masks ?? new int[0]);
        prev = new Dictionary<string, RenderTexture>();
        masks = new Dictionary<(int, int), RenderTexture>();
        if (pixels)
        {
            var sh = ShaderUtil.CreateShaderAsset(DiffShader, false);  // in memory, nothing is written to the project
            if (sh == null || !sh.isSupported)
                return Fail("pixel diff shader does not compile");
            diffMat = new Material(sh) { hideFlags = HideFlags.HideAndDontSave };
        }
        data = Activator.CreateInstance(dataType);
        events = new List<string>();
        state = 0; waited = 0; waitedPixels = 0; stable = 0; lastCount = -1; index = 0; pending = -1; pendingPixels = null;
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
                winType.GetMethod("RepaintAllNeededThings", I)?.Invoke(window, null);
                if (EditorApplication.timeSinceStartup - started > 60)
                    throw new Exception("the Frame Debugger did not capture a frame in 60 s: make the Game view visible " +
                                        "(or enter Play Mode) and retry");
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
                    if (++waited > 300) throw new Exception($"no data for event {index}");
                    return;
                }
                if (pixels && pendingPixels == null)
                    pendingPixels = Pixels();  // the Frame Debugger keeps its own copy of the event target
                waited = waitedPixels = 0;
                var px = pixels ? pendingPixels ?? "{\"method\": \"not-captured\"}" : "null";
                events.Add(EventJson(index, px));
                index++;
                if (index % 25 == 0) Progress($"events {index}/{count}");
                if (index >= count) { Finish(); return; }
                Request(index);
            }
        }
        catch (Exception e)
        {
            Stop();
            File.WriteAllText(Path.Combine(outDir, "frame.error"), (e.InnerException ?? e).ToString());
        }
    }

    // replay up to event i (limit = i + 1); Tick reads its data and pixels once the replay is there
    static void Request(int i)
    {
        pending = i;
        pendingPixels = null;
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
        File.WriteAllText(Path.Combine(outDir, "frame.json"), sb.ToString());
        Progress("done");
    }

    static string Pixels()
    {
        var rt = dataType.GetField("m_RenderTargetRenderTexture", I)?.GetValue(data) as RenderTexture;
        if (rt == null || !rt.IsCreated())
            return "{\"method\": \"no-rt\"}";
        if (rt.antiAliasing > 1 || rt.dimension != UnityEngine.Rendering.TextureDimension.Tex2D)
            return $"{{\"method\": \"unsupported\", \"msaa\": {rt.antiAliasing}, \"dimension\": {Str(rt.dimension.ToString())}}}";
        int w = rt.width, h = rt.height;
        var key = (dataType.GetField("m_RenderTargetName", I)?.GetValue(data) as string ?? rt.name) + $"|{w}x{h}|{rt.graphicsFormat}|{rt.depthStencilFormat}";
        string method = "diff";
        if (!prev.TryGetValue(key, out var before) || before.width != w || before.height != h)
        {
            if (before != null) { before.Release(); UnityEngine.Object.DestroyImmediate(before); }
            var desc = rt.descriptor;
            desc.msaaSamples = 1; desc.useMipMap = false; desc.autoGenerateMips = false; desc.memoryless = RenderTextureMemoryless.None;
            before = new RenderTexture(desc) { hideFlags = HideFlags.HideAndDontSave };
            before.Create();
            var active = RenderTexture.active;
            RenderTexture.active = before;
            GL.Clear(true, true, Color.clear);
            RenderTexture.active = active;
            prev[key] = before;
            method = "first-use";
        }
        if (!masks.TryGetValue((w, h), out var mask))
        {
            mask = new RenderTexture(w, h, 0, RenderTextureFormat.R8) { hideFlags = HideFlags.HideAndDontSave };
            mask.Create();
            masks[(w, h)] = mask;
        }
        long changed = Count(rt, before, mask, out bool error);
        if (dumpMasks.Contains(pending)) DumpMask(mask, pending);
        Graphics.CopyTexture(rt, before);
        long self = Count(rt, before, mask, out _);  // must be 0: the comparison itself works
        var req = new { hasError = error };
        return $"{{\"method\": {Str(req.hasError ? "readback-error" : method)}, \"changed\": {changed}, \"width\": {w}, \"height\": {h}, " +
               $"\"format\": {Str(rt.graphicsFormat.ToString())}, \"depth_format\": {Str(rt.depthStencilFormat.ToString())}, \"rt_key\": {Str(key)}, \"self_check\": {self}}}";
    }

    static long Count(RenderTexture cur, RenderTexture before, RenderTexture mask, out bool error)
    {
        diffMat.SetTexture("_Cur", cur);
        diffMat.SetTexture("_Prev", before);
        diffMat.SetFloat("_Eps", eps);
        diffMat.SetFloat("_Neighborhood", neighborhood ? 1 : 0);
        Graphics.Blit(null, mask, diffMat, 0);
        if (minNeighbors > 1)
        {
            if (mask2 == null || mask2.width != mask.width || mask2.height != mask.height)
            {
                if (mask2 != null) { mask2.Release(); UnityEngine.Object.DestroyImmediate(mask2); }
                mask2 = new RenderTexture(mask.width, mask.height, 0, RenderTextureFormat.R8) { hideFlags = HideFlags.HideAndDontSave };
                mask2.Create();
            }
            diffMat.SetTexture("_Mask", mask);
            diffMat.SetFloat("_MinNeighbors", minNeighbors);
            Graphics.Blit(null, mask2, diffMat, 1);
            Graphics.CopyTexture(mask2, mask);
        }
        var req = UnityEngine.Rendering.AsyncGPUReadback.Request(mask, 0, TextureFormat.R8);
        req.WaitForCompletion();
        error = req.hasError;
        if (error) return -1;
        long n = 0;
        var bytes = req.GetData<byte>();
        for (int k = 0; k < bytes.Length; k++)
            if (bytes[k] != 0) n++;
        return n;
    }

    static void DumpMask(RenderTexture mask, int i)
    {
        var tex = new Texture2D(mask.width, mask.height, TextureFormat.R8, false);
        var active = RenderTexture.active;
        RenderTexture.active = mask;
        tex.ReadPixels(new Rect(0, 0, mask.width, mask.height), 0, 0);
        RenderTexture.active = active;
        File.WriteAllBytes(Path.Combine(outDir, $"mask_{i}.png"), tex.EncodeToPNG());
        UnityEngine.Object.DestroyImmediate(tex);
    }

    static void Cleanup()
    {
        foreach (var t in (prev?.Values ?? Enumerable.Empty<RenderTexture>()).Concat(masks?.Values ?? Enumerable.Empty<RenderTexture>()))
            if (t != null) { t.Release(); UnityEngine.Object.DestroyImmediate(t); }
        prev?.Clear();
        masks?.Clear();
        if (mask2 != null) { mask2.Release(); UnityEngine.Object.DestroyImmediate(mask2); mask2 = null; }
        if (diffMat != null) { var sh = diffMat.shader; UnityEngine.Object.DestroyImmediate(diffMat); UnityEngine.Object.DestroyImmediate(sh); }
        diffMat = null;
    }

    static void Stop()
    {
        EditorApplication.update -= Tick;
        try { Cleanup(); } catch { }
        if (SessionState.GetString(TokenKey, "") == token)
            SessionState.EraseString(TokenKey);
        try
        {
            if (wasEnabled) SetLimit((int)Prop("count"));
            else winType.GetMethod("DisableFrameDebugger", I).Invoke(window, null);
            if (openedWindow) ((EditorWindow)window).Close();
        }
        catch { }
    }

    static string EventJson(int i, string pixelsJson)
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
        sb.Append(", \"pixels\": ").Append(pixelsJson);
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
        var m = winType.GetMethods(I).FirstOrDefault(x => x.Name == "ChangeFrameEventLimit" && x.GetParameters().Length == 1);
        if (m != null && window != null) m.Invoke(window, new object[] { n });
        else util.GetProperty("limit", S).SetValue(null, n);
    }

    static object Prop(string name) => util.GetProperty(name, S).GetValue(null);

    static object Call(string name, params object[] args)
    {
        var m = util.GetMethods(S).First(x => x.Name == name && x.GetParameters().Length == args.Length);
        return m.Invoke(null, args);
    }

    static void Progress(string s) => File.WriteAllText(Path.Combine(outDir, "frame.progress"), s);

    static string Fail(string msg)
    {
        File.WriteAllText(Path.Combine(outDir, "frame.error"), msg);
        return "error: " + msg;
    }

    static string Str(string v)
    {
        if (v == null)
            return "null";
        var sb = new StringBuilder("\"");
        foreach (var c in v)
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
            else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }
}
