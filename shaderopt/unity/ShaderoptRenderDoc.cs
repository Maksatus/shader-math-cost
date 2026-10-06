// RenderDoc capture of the Game view (plan item K1.3: the reference for the pixel counts).
//
// Used by `python -m shaderopt renderdoc`:
//   unity command run_script --file ShaderoptRenderDoc.cs --entry ShaderoptRenderDoc.Start --args '["<config.json>"]'
// Needs RenderDoc loaded into the editor (Game tab menu -> Load RenderDoc). Start() pauses Play Mode if asked,
// enables the Frame Debugger at its last event (it re-renders the whole game frame inside the Game view repaint)
// and records that repaint like the Game view RenderDoc button; the Frame Debugger is restored after.
// Writes <out>/renderdoc.json: {"capture": "<new .rdc>"}.
// Pause() / Resume() (entry points) set the pause state.
//
// config.json: {"out": "<absolute folder>", "pause": true}

using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

public static class ShaderoptRenderDoc
{
    [Serializable]
    class Config
    {
        public string @out = "";
        public bool pause = true;
    }

    const BindingFlags S = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    const BindingFlags I = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    const string TokenKey = "shaderopt.renderdoc.token";
    static Type rd;
    static EditorWindow gameView;
    static string outDir, token;
    static int state, ticks, frame;
    static bool pauseAfter, fdOpened, fdWasEnabled, wasPaused;
    static Type fdUtil, fdWinType;
    static UnityEngine.Object fdWindow;

    static string oldTemplate;

    static void RestoreTemplate()
    {
        try { if (oldTemplate != null) RdApi.SetTemplate(oldTemplate); } catch { }
        oldTemplate = null;
    }

    static int FdCount() => (int)fdUtil.GetProperty("count", S).GetValue(null);

    static void RestoreFrameDebugger()
    {
        try
        {
            if (fdWindow != null && !fdWasEnabled) fdWinType.GetMethod("DisableFrameDebugger", I).Invoke(fdWindow, null);
            if (fdWindow != null && fdOpened) ((EditorWindow)fdWindow).Close();
        }
        catch { }
        fdWindow = null;
    }
    static DateTime started;

    public static string Start(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(File.ReadAllText(configPath));
        outDir = cfg.@out;
        Directory.CreateDirectory(outDir);
        foreach (var f in new[] { "renderdoc.json", "renderdoc.error" })
            if (File.Exists(Path.Combine(outDir, f))) File.Delete(Path.Combine(outDir, f));
        rd = typeof(EditorWindow).Assembly.GetType("UnityEditorInternal.RenderDoc");
        if (rd == null || !(bool)rd.GetMethod("IsLoaded", S, null, Type.EmptyTypes, null).Invoke(null, null))
            return Fail("RenderDoc is not loaded: Game tab menu -> Load RenderDoc");
        var gvType = typeof(EditorWindow).Assembly.GetType("UnityEditor.GameView");
        gameView = Resources.FindObjectsOfTypeAll(gvType).FirstOrDefault() as EditorWindow;
        if (gameView == null)
            return Fail("no Game view");
        pauseAfter = cfg.pause;
        fdWindow = null;
        wasPaused = EditorApplication.isPaused;
        if (cfg.pause && EditorApplication.isPlaying) EditorApplication.isPaused = true;
        token = Guid.NewGuid().ToString();
        SessionState.SetString(TokenKey, token);
        state = 0; ticks = 0;
        started = DateTime.Now;
        EditorApplication.update -= Tick;
        EditorApplication.update += Tick;
        return "started";
    }

    public static string Pause(string on)
    {
        EditorApplication.isPaused = on == "true";
        return "paused=" + EditorApplication.isPaused;
    }

    static void Tick()
    {
        if (SessionState.GetString(TokenKey, "") != token) { EditorApplication.update -= Tick; return; }
        try
        {
            ticks++;
            if (state == 0)
            {
                if (ticks == 1) gameView.ShowTab();  // the Game view must be the visible tab of its dock area
                if (ticks < 5) { gameView.Repaint(); return; }  // let the pause and the tab switch settle
                // The Frame Debugger re-renders the game frame inside the Game view repaint; with its limit at the
                // last event that repaint is the whole frame — and exactly the frame the Frame Debugger snapshot
                // reads. GUIView.CaptureRenderDocScene() (the Game view RenderDoc button) records that repaint.
                // (A plain frame capture fails in the editor: buffers stay mapped across editor frames.)
                if (fdWindow == null)
                {
                    var asm = typeof(EditorWindow).Assembly;
                    fdUtil = AppDomain.CurrentDomain.GetAssemblies().Select(x => x.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerUtility")).First(t => t != null);
                    fdWinType = asm.GetType("UnityEditor.FrameDebuggerWindow");
                    fdWindow = Resources.FindObjectsOfTypeAll(fdWinType).FirstOrDefault();
                    fdOpened = fdWindow == null;
                    if (fdOpened) { fdWinType.GetMethod("OpenWindow", S).Invoke(null, null); fdWindow = Resources.FindObjectsOfTypeAll(fdWinType).First(); }
                    fdWasEnabled = FdCount() > 0;
                    if (!fdWasEnabled) fdWinType.GetMethod("EnableFrameDebugger", I).Invoke(fdWindow, null);
                    ticks = 0;
                    return;
                }
                EditorApplication.QueuePlayerLoopUpdate();
                gameView.Repaint();
                int n = FdCount();
                if (n == 0 || ticks < 10) { if (ticks > 600) throw new Exception("the Frame Debugger did not capture a frame"); return; }
                fdWinType.GetMethods(I).First(m => m.Name == "ChangeFrameEventLimit" && m.GetParameters().Length == 1).Invoke(fdWindow, new object[] { n });
                // write the capture straight into the output folder (the previous template is restored after)
                oldTemplate = RdApi.GetTemplate();
                RdApi.SetTemplate(Path.Combine(outDir, "frame"));
                var host = typeof(EditorWindow).GetField("m_Parent", I)?.GetValue(gameView);
                host.GetType().GetMethod("CaptureRenderDocScene", I).Invoke(host, null);
                gameView.Repaint();
                frame = n;
                state = 2; ticks = 0;
                return;
            }
            // find the new capture: RenderDoc writes it to its temp folder
            var dirs = new[] { outDir, Path.Combine(Path.GetTempPath(), "RenderDoc") };
            var cap = dirs.Where(Directory.Exists)
                .SelectMany(d => Directory.GetFiles(d, "*.rdc", SearchOption.TopDirectoryOnly))
                .Select(f => new FileInfo(f)).Where(f => f.LastWriteTime >= started.AddSeconds(-1))
                .OrderByDescending(f => f.LastWriteTime).FirstOrDefault();
            if (cap == null)
            {
                if (ticks > 600) throw new Exception("no new .rdc capture found in " + string.Join(", ", dirs));
                return;
            }
            EditorApplication.update -= Tick;
            RestoreFrameDebugger();
            RestoreTemplate();
            File.WriteAllText(Path.Combine(outDir, "renderdoc.frames"), $"frame debugger events {frame}");
            File.WriteAllText(Path.Combine(outDir, "renderdoc.json"),
                "{\"capture\": \"" + cap.FullName.Replace("\\", "\\\\") + "\", \"bytes\": " + cap.Length + ", \"was_paused\": " + (wasPaused ? "true" : "false") + "}");
        }
        catch (Exception e)
        {
            EditorApplication.update -= Tick;
            RestoreFrameDebugger();
            RestoreTemplate();
            File.WriteAllText(Path.Combine(outDir, "renderdoc.error"), (e.InnerException ?? e).ToString());
        }
    }

    static string Fail(string msg)
    {
        File.WriteAllText(Path.Combine(outDir, "renderdoc.error"), msg);
        return "error: " + msg;
    }

    // RENDERDOC_API_1_6_0 is a table of function pointers (renderdoc_app.h): entry 11 SetCaptureFilePathTemplate,
    // 12 GetCaptureFilePathTemplate. RenderDoc copies the string.
    static class RdApi
    {
        [System.Runtime.InteropServices.DllImport("renderdoc.dll")]
        static extern int RENDERDOC_GetAPI(int version, out IntPtr api);

        [System.Runtime.InteropServices.UnmanagedFunctionPointer(System.Runtime.InteropServices.CallingConvention.Cdecl)]
        delegate void SetFn(IntPtr pathUtf8);

        [System.Runtime.InteropServices.UnmanagedFunctionPointer(System.Runtime.InteropServices.CallingConvention.Cdecl)]
        delegate IntPtr GetFn();

        static IntPtr Fn(int i)
        {
            if (RENDERDOC_GetAPI(10600, out var table) != 1 || table == IntPtr.Zero)
                throw new Exception("RENDERDOC_GetAPI failed");
            return System.Runtime.InteropServices.Marshal.ReadIntPtr(table, i * IntPtr.Size);
        }

        public static string GetTemplate() =>
            System.Runtime.InteropServices.Marshal.PtrToStringUTF8(
                System.Runtime.InteropServices.Marshal.GetDelegateForFunctionPointer<GetFn>(Fn(12))());

        public static void SetTemplate(string path)
        {
            var p = System.Runtime.InteropServices.Marshal.StringToCoTaskMemUTF8(path);
            try { System.Runtime.InteropServices.Marshal.GetDelegateForFunctionPointer<SetFn>(Fn(11))(p); }
            finally { System.Runtime.InteropServices.Marshal.FreeCoTaskMem(p); }
        }
    }
}
