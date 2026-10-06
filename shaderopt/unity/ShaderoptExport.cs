// Export compiled shader variants the way Inspector -> "Compile and show code" does
// (only variants that would be included into the build), for the chosen platforms.
//
// Used by `python -m shaderopt export`, two ways:
//   open editor:  unity command run_script --file ShaderoptExport.cs --entry ShaderoptExport.Run --args '["<config.json>"]'
//   closed:       Unity.exe -batchmode -quit -projectPath <p> -executeMethod ShaderoptExport.Batch -shaderoptConfig <config.json>
//                 (the file is copied into Assets/Editor for the run and removed afterwards)
//
// config.json: {"shaders": ["Assets/...shadergraph", "Assets/Folder", "Shader/Name"], "platforms": ["gles3", "vulkan"],
//               "out": "<absolute folder>"}
// Writes <out>/<shader name>.shader (the "Compile and show code" text) and <out>/export.json.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;

public static class ShaderoptExport
{
    [Serializable]
    class Config
    {
        public string[] shaders = new string[0];
        public string[] platforms = { "gles3", "vulkan" };
        public string @out = "";
    }

    [Serializable]
    class Item
    {
        public string shader;
        public string asset;
        public string file;
        public string error;
    }

    [Serializable]
    class Result
    {
        public string unity;
        public string project;
        public List<Item> items = new List<Item>();
        public List<string> errors = new List<string>();
    }

    const int CustomPlatforms = 3;  // OpenCompiledShader mode: only the platforms in the mask

    static readonly Dictionary<string, ShaderCompilerPlatform> Platforms = new Dictionary<string, ShaderCompilerPlatform>
    {
        { "gles3", ShaderCompilerPlatform.GLES3x },
        { "vulkan", ShaderCompilerPlatform.Vulkan },
    };

    // run_script entry point (open editor); returns export.json content
    public static string Run(string configPath)
    {
        return ToJson(Export(configPath));
    }

    // -executeMethod entry point (batchmode)
    public static void Batch()
    {
        var args = Environment.GetCommandLineArgs();
        int i = Array.IndexOf(args, "-shaderoptConfig");
        if (i < 0 || i + 1 >= args.Length)
        {
            Debug.LogError("shaderopt: -shaderoptConfig <config.json> is missing");
            EditorApplication.Exit(2);
            return;
        }
        var r = Export(args[i + 1]);
        EditorApplication.Exit(r.items.Any(x => x.error == null) && r.errors.Count == 0 ? 0 : 1);
    }

    static Result Export(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(File.ReadAllText(configPath));
        var res = new Result { unity = Application.unityVersion, project = Path.GetDirectoryName(Application.dataPath) };
        Directory.CreateDirectory(cfg.@out);

        int mask = 0;
        foreach (var p in cfg.platforms)
        {
            if (Platforms.TryGetValue(p, out var sp))
                mask |= 1 << (int)sp;
            else
                res.errors.Add($"unknown platform '{p}' (known: {string.Join(", ", Platforms.Keys)})");
        }

        var open = typeof(ShaderUtil).GetMethod("OpenCompiledShader", BindingFlags.Static | BindingFlags.NonPublic);
        if (open == null)
            res.errors.Add("ShaderUtil.OpenCompiledShader not found in this Unity version");

        if (mask != 0 && open != null)
        {
            foreach (var (shader, asset) in Resolve(cfg.shaders, res.errors))
            {
                var item = new Item { shader = shader.name, asset = asset };
                res.items.Add(item);
                try
                {
                    item.file = CompileOne(open, shader, mask, cfg.@out);
                }
                catch (Exception e)
                {
                    item.error = (e.InnerException ?? e).Message;
                }
            }
        }
        File.WriteAllText(Path.Combine(cfg.@out, "export.json"), ToJson(res));
        return res;
    }

    // JsonUtility does not serialize lists of this script's own types when run_script
    // compiles it into an in-memory assembly, so the result is written by hand
    static string Str(string v)
    {
        if (v == null)
            return "null";
        var sb = new System.Text.StringBuilder("\"");
        foreach (var c in v)
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
            else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }

    static string ToJson(Result r)
    {
        var items = r.items.Select(i => "{\"shader\": " + Str(i.shader) + ", \"asset\": " + Str(i.asset) +
                                        ", \"file\": " + Str(i.file) + ", \"error\": " + Str(i.error) + "}");
        var nl = Environment.NewLine;
        return "{\"unity\": " + Str(r.unity) + ", \"project\": " + Str(r.project) + "," + nl +
               " \"items\": [" + nl + "  " + string.Join("," + nl + "  ", items) + "]," + nl +
               " \"errors\": [" + string.Join(", ", r.errors.Select(Str)) + "]}" + nl;
    }

    static string CompileOne(MethodInfo open, Shader shader, int mask, string outDir)
    {
        // Unity writes Temp/Compiled-<shader name with '/' -> '-'>.shader
        var temp = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp");
        var src = Path.Combine(temp, "Compiled-" + shader.name.Replace('/', '-') + ".shader");
        if (File.Exists(src))
            File.Delete(src);
        open.Invoke(null, new object[] { shader, CustomPlatforms, mask, false, false, true });
        if (!File.Exists(src))
            throw new Exception("Unity did not write " + src);
        var dst = Path.Combine(outDir, Safe(shader.name) + ".shader");
        File.Copy(src, dst, true);
        return dst;
    }

    static IEnumerable<(Shader, string)> Resolve(string[] specs, List<string> errors)
    {
        var seen = new HashSet<Shader>();
        foreach (var spec in specs)
        {
            var found = new List<(Shader, string)>();
            if (AssetDatabase.IsValidFolder(spec))
            {
                foreach (var guid in AssetDatabase.FindAssets("t:Shader", new[] { spec }))
                {
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    foreach (var s in AssetDatabase.LoadAllAssetsAtPath(path).OfType<Shader>())
                        found.Add((s, path));
                }
            }
            else if (spec.StartsWith("Assets/") || spec.StartsWith("Packages/"))
            {
                var s = AssetDatabase.LoadAssetAtPath<Shader>(spec);
                if (s != null)
                    found.Add((s, spec));
            }
            else
            {
                var s = Shader.Find(spec);
                if (s != null)
                    found.Add((s, AssetDatabase.GetAssetPath(s)));
            }
            if (found.Count == 0)
                errors.Add($"no shader found for '{spec}'");
            foreach (var f in found)
                if (seen.Add(f.Item1))
                    yield return f;
        }
    }

    static string Safe(string name)
    {
        var bad = Path.GetInvalidFileNameChars();
        return new string(name.Select(c => c == '/' || bad.Contains(c) ? '_' : c).ToArray());
    }
}
