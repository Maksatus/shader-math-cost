// One run of a ParetoGPU entry in the editor: the protocol of adapters/unity/bridge.py, compiled into every script
// (bridge.py puts this file in front of the script it runs).
//
// Python writes <out>/<name>_config.json with "out" (absolute folder) and "name"; the entry opens the job with it and
// answers in <out>/<name>.json, reports progress in <name>.progress and fails with <name>.error: "[code] message"
// (model/errors.py: the UI has a hint for the code; "error" leaves the caller's own). An entry that works on editor
// ticks returns "started" at once, the others return when the answer is written.

using System;
using System.IO;
using System.Text;
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

    // the config text, for the script's own Config; the files of an earlier run of the job are removed
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
