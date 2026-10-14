// Job protocol of adapters/unity/bridge.py, prepended to every script. Config <out>/<name>_config.json {"out"
// (absolute), "name", ...}; answer <out>/<name>.json, progress <name>.progress, failure <name>.error "[code] message"
// (model/errors.py codes; "error" = the caller's own). Tick-driven entries return "started" at once, the others after
// the answer.

using System;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class ParetoGpuJob
{
    [Serializable]
    class Head
    {
        public string @out = "";
        public string name = "";
    }

    public class Failure : Exception
    {
        public readonly string Code;

        public Failure(string code, string message) : base(message)
        {
            Code = code;
        }
    }

    public static string Folder { get; private set; }
    static string name;

    // -> the config text for the script's own Config; clears files of an earlier run
    public static string Open(string configPath)
    {
        var text = File.ReadAllText(configPath);
        var head = JsonUtility.FromJson<Head>(text);
        Folder = head.@out;
        name = head.name;
        Directory.CreateDirectory(Folder);
        foreach (var ext in new[] { ".json", ".progress", ".error" })
            if (File.Exists(PathOf(ext))) File.Delete(PathOf(ext));
        return text;
    }

    static bool? background;

    public static void KeepRenderingInBackground()
    {
        if (!EditorApplication.isPlaying || background != null)
            return;
        background = Application.runInBackground;
        Application.runInBackground = true;
    }

    public static void RestoreBackground()
    {
        if (background != null && EditorApplication.isPlaying)
            Application.runInBackground = background.Value;
        background = null;
    }

    public static string PathOf(string ext) => Path.Combine(Folder, name + ext);

    public static void Progress(string text) => File.WriteAllText(PathOf(".progress"), text);

    public static string Done(string json)
    {
        var tmp = PathOf(".json.tmp");
        File.WriteAllText(tmp, json);
        if (File.Exists(PathOf(".json"))) File.Delete(PathOf(".json"));
        File.Move(tmp, PathOf(".json"));
        return "ok";
    }

    public static string Fail(string code, string message)
    {
        File.WriteAllText(PathOf(".error"), $"[{code}] {message}");
        return "error";
    }

    public static string Fail(Exception e)
    {
        var x = e.InnerException ?? e;
        return x is Failure f ? Fail(f.Code, f.Message) : Fail("error", x.ToString());
    }

    public static string Str(string v)
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
