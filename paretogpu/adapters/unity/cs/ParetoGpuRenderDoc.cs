// RenderDoc capture of the Game view (plan item K1.3: the reference for the pixel counts).
//
// Used by `python -m paretogpu frame`:
//   adapters/unity/bridge.py call(..., "ParetoGpuRenderDoc.cs", "Start", <out>, "renderdoc", ...)
// Needs RenderDoc loaded into the editor (Game tab menu -> Load RenderDoc). Start() pauses Play Mode if asked,
// enables the Frame Debugger at its last event (it re-renders the whole game frame inside the Game view repaint)
// and records that repaint like the Game view RenderDoc button; the Frame Debugger is restored after. The Frame
// Debugger is driven through FrameDebuggerUtility alone (as its window does), no window is opened.
// Answers {"capture": "<new .rdc>", "bytes", "was_paused"} (cs/ParetoGpuJob.cs: <out>/renderdoc.json).
// Pause() (an entry point outside the job protocol) sets the pause state.
//
// config: {"pause": true}

using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

public static class ParetoGpuRenderDoc
{
    [Serializable]
    class Config
    {
        public bool pause = true;
    }

    const BindingFlags S = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    const BindingFlags I = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    const string TokenKey = "paretogpu.renderdoc.token";
    static Type rd;
    static EditorWindow gameView;
    static string outDir, token;
    static int state, ticks, frame;
    static bool pauseAfter, fdOn, fdWasEnabled, wasPaused;
    static Type fdUtil;

    static string oldTemplate;

    static void RestoreTemplate()
    {
        try { if (oldTemplate != null) RdApi.SetTemplate(oldTemplate); } catch { }
        oldTemplate = null;
    }

    // will the next Game view repaint render the cameras (and so the Frame Debugger replay) itself
    static bool RenderInRepaint() =>
        typeof(EditorWindow).Assembly.GetType("UnityEditor.PlayModeView")?.GetProperty("renderViewCallNeededInOnGUI", S)
            ?.GetValue(null) is bool b ? b : true;

    static int FdCount() => (int)fdUtil.GetProperty("count", S).GetValue(null);

    static object FdCall(string name, params object[] args) =>
        fdUtil.GetMethods(S).First(m => m.Name == name && m.GetParameters().Length == args.Length).Invoke(null, args);

    // what the Frame Debugger window does after a change (RepaintAllNeededThings): the replay shows on the next repaint
    static void SceneRepaintDirty() => typeof(EditorApplication).GetMethod("SetSceneRepaintDirty", S)?.Invoke(null, null);

    static void ChangeLimit(int limit)
    {
        fdUtil.GetProperty("limit", S).SetValue(null, limit);
        SceneRepaintDirty();
    }

    static void RestoreFrameDebugger()
    {
        try
        {
            if (fdOn && !fdWasEnabled)
            {
                FdCall("SetEnabled", false, (int)FdCall("GetRemotePlayerGUID"));
                SceneRepaintDirty();
            }
        }
        catch { }
        fdOn = false;
    }
    static DateTime started, limitChanged, phase;

    static double Waited() => (DateTime.Now - phase).TotalSeconds;

    public static string Start(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(ParetoGpuJob.Open(configPath));
        outDir = ParetoGpuJob.Folder;
        if (!EditorApplication.isPlaying)
            return ParetoGpuJob.Fail("not_playing", "the editor is not in Play Mode: an Edit Mode frame is not the game's");
        rd = typeof(EditorWindow).Assembly.GetType("UnityEditorInternal.RenderDoc");
        if (rd == null || !(bool)rd.GetMethod("IsLoaded", S, null, Type.EmptyTypes, null).Invoke(null, null))
            return ParetoGpuJob.Fail("renderdoc", "RenderDoc is not loaded: Game tab menu -> Load RenderDoc");
        var gvType = typeof(EditorWindow).Assembly.GetType("UnityEditor.GameView");
        gameView = Resources.FindObjectsOfTypeAll(gvType).FirstOrDefault() as EditorWindow;
        if (gameView == null)
            return ParetoGpuJob.Fail("no_frame", "no Game view: open the Game tab");
        pauseAfter = cfg.pause;
        fdOn = false;
        wasPaused = EditorApplication.isPaused;
        if (cfg.pause && EditorApplication.isPlaying) EditorApplication.isPaused = true;
        ParetoGpuJob.KeepRenderingInBackground();
        token = Guid.NewGuid().ToString();
        SessionState.SetString(TokenKey, token);
        state = 0; ticks = 0;
        started = phase = DateTime.Now;
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
                if (!fdOn)
                {
                    fdUtil = AppDomain.CurrentDomain.GetAssemblies().Select(x => x.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerUtility")).First(t => t != null);
                    fdWasEnabled = FdCount() > 0;
                    if (!fdWasEnabled)
                    {
                        if (EditorApplication.isPlaying && !EditorApplication.isPaused) EditorApplication.isPaused = true;
                        FdCall("SetEnabled", true, UnityEditorInternal.ProfilerDriver.connectedProfiler);
                    }
                    fdOn = true;
                    ticks = 0;
                    phase = DateTime.Now;
                    return;
                }
                EditorApplication.QueuePlayerLoopUpdate();
                SceneRepaintDirty();
                gameView.Repaint();
                int n = FdCount();
                if (n == 0 || ticks < 10)
                {
                    if (ticks % 50 == 0)
                        ParetoGpuJob.Progress($"waiting for the Frame Debugger: {n} events, {Waited():0} s");
                    if (Waited() > 60)
                        throw new ParetoGpuJob.Failure("no_frame", "the Frame Debugger did not capture a frame: enter Play " +
                                                       "Mode and make the Game view visible");
                    return;
                }
                // A Frame Debugger already at its last event does not replay the frame: the repaint only blits its
                // cached image and the capture holds the editor UI alone. One event back now, the last one in
                // state 1: the limit changes, so the captured repaint replays the whole frame.
                ChangeLimit(Math.Max(1, n - 1));
                frame = n;
                state = 1; ticks = 0;
                limitChanged = phase = DateTime.Now;
                return;
            }
            if (state == 1)
            {
                // No QueuePlayerLoopUpdate from here on: in Play Mode the Game view renders its cameras inside its
                // own repaint only when PlayModeView.renderViewCallNeededInOnGUI (NeedToPerformRendering and no
                // player loop update waiting); a queued update renders the frame in the player loop instead, outside
                // the repaint CaptureRenderDocScene records, and the capture holds the editor UI alone.
                gameView.Repaint();
                // editor ticks can be a few ms apart: give the replay at the previous event real time too
                if (ticks < 5 || (DateTime.Now - limitChanged).TotalSeconds < 0.3) return;
                if (!RenderInRepaint() && Waited() < 10) return;
                ChangeLimit(frame);
                // write the capture straight into the output folder (the previous template is restored after)
                oldTemplate = RdApi.GetTemplate();
                RdApi.SetTemplate(Path.Combine(outDir, "frame"));
                var host = typeof(EditorWindow).GetField("m_Parent", I)?.GetValue(gameView);
                host.GetType().GetMethod("CaptureRenderDocScene", I).Invoke(host, null);
                gameView.Repaint();
                state = 2; ticks = 0;
                phase = DateTime.Now;
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
                if (Waited() > 60) throw new ParetoGpuJob.Failure("renderdoc", "no new .rdc capture found in " + string.Join(", ", dirs));
                return;
            }
            EditorApplication.update -= Tick;
            RestoreFrameDebugger();
            RestoreTemplate();
            ParetoGpuJob.RestoreBackground();
            File.WriteAllText(Path.Combine(outDir, "renderdoc.frames"), $"frame debugger events {frame}");
            ParetoGpuJob.Done("{\"capture\": " + ParetoGpuJob.Str(cap.FullName) + ", \"bytes\": " + cap.Length +
                              ", \"was_paused\": " + (wasPaused ? "true" : "false") + "}");
        }
        catch (Exception e)
        {
            EditorApplication.update -= Tick;
            RestoreFrameDebugger();
            RestoreTemplate();
            ParetoGpuJob.RestoreBackground();
            if (EditorApplication.isPlaying) EditorApplication.isPaused = wasPaused;
            ParetoGpuJob.Fail(e);
        }
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
