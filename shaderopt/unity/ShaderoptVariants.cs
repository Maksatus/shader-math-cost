// Compile exact shader variants (shader x subshader x pass x keywords) for malioc (plan item K1.4):
// the variants a Frame Debugger snapshot used, with the keywords it reported, whether or not
// "Compile and show code" (ShaderoptExport.cs) would include them.
//
// Used by `python -m shaderopt cost` (frame/variants.py):
//   unity command run_script --file ShaderoptVariants.cs --entry ShaderoptVariants.Run --args '["<config.json>"]'
//
// config.json: one entry per variant in parallel arrays (JsonUtility in a run_script assembly reads arrays of
// strings and numbers, not arrays of this script's classes); keywords are space separated, id = array index:
//   {"shaders": ["Custom/Lit"], "subshaders": [0], "pass_indices": [0], "passes": ["Forward Opaque"],
//    "keywords": ["_EMISSION _NORMALMAP"], "platforms": ["gles3", "vulkan"], "out": "<absolute folder>"}
// Compute kernels (subshader -1) are refused: ComputeShader.FindKernel / HasKernel and the internal
// ShaderUtil.CompileComputeShaderVariant crashed the 6000.3.18f1 editor when called from here.
// Writes <out>/<id>_<platform>_<vert|frag>.bin (GLSL text for gles3, SPIR-V binary for vulkan: what
// ShaderData.Pass.CompileVariant(..., forExternalTool: true) returns for Android) and <out>/variants_result.json
// with the fingerprint of every variant's shader (see Fingerprint).
//
// Fingerprints entry: config {"shaders": [...], "out": "<file.json>"} -> {"unity": ..., "shaders": {name: fingerprint}}
// without compiling anything; frame/variants.py compares them with the fingerprints stored next to the compiled
// files and compiles a variant again when its shader, an include or package it depends on, the Unity version or
// the Android platform defines changed.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;

public static class ShaderoptVariants
{
    class Variant
    {
        public int id;
        public string shader = "";
        public int subshader;
        public int pass_index;
        public string pass = "";
        public string[] keywords = new string[0];
    }

    [Serializable]
    class Config
    {
        public string[] shaders = new string[0];
        public int[] subshaders = new int[0];
        public int[] pass_indices = new int[0];
        public string[] passes = new string[0];
        public string[] keywords = new string[0];
        public string[] platforms = { "gles3", "vulkan" };
        public string @out = "";
    }

    static readonly Dictionary<string, ShaderCompilerPlatform> Platforms = new Dictionary<string, ShaderCompilerPlatform>
    {
        { "gles3", ShaderCompilerPlatform.GLES3x },
        { "vulkan", ShaderCompilerPlatform.Vulkan },
    };

    static readonly (ShaderType type, string ext)[] Stages = { (ShaderType.Vertex, "vert"), (ShaderType.Fragment, "frag") };

    public static string Run(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(File.ReadAllText(configPath));
        Directory.CreateDirectory(cfg.@out);
        var byName = new Dictionary<string, Shader>();
        var lines = new List<string>();
        var variants = Enumerable.Range(0, cfg.shaders.Length).Select(i => new Variant
        {
            id = i, shader = cfg.shaders[i], subshader = cfg.subshaders[i], pass_index = cfg.pass_indices[i],
            pass = cfg.passes[i], keywords = cfg.keywords[i].Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries),
        }).ToList();
        foreach (var v in variants)
        {
            var files = new List<string>();
            var errors = new List<string>();
            string fingerprint = null;
            try
            {
                if (v.subshader < 0)
                    throw new Exception("compute kernels are not compiled (the compute API crashes the editor from a script)");
                var shader = Find(v.shader, byName);
                if (shader == null)
                    throw new Exception("shader not found");
                fingerprint = Fingerprint(shader);
                var data = ShaderUtil.GetShaderData(shader);
                if (v.subshader < 0 || v.subshader >= data.SubshaderCount)
                    throw new Exception($"no subshader {v.subshader} (has {data.SubshaderCount})");
                var sub = data.GetSubshader(v.subshader);
                if (v.pass_index < 0 || v.pass_index >= sub.PassCount)
                    throw new Exception($"no pass {v.pass_index} in subshader {v.subshader} (has {sub.PassCount})");
                var pass = sub.GetPass(v.pass_index);
                foreach (var plat in cfg.platforms)
                {
                    if (!Platforms.TryGetValue(plat, out var sp))
                    {
                        errors.Add($"unknown platform {plat}");
                        continue;
                    }
                    foreach (var (type, ext) in Stages)
                    {
                        if (!pass.HasShaderStage(type))
                            continue;
                        var r = pass.CompileVariant(type, v.keywords, sp, BuildTarget.Android, true);
                        var msgs = string.Join("; ", r.Messages.Where(m => m.severity == ShaderCompilerMessageSeverity.Error)
                                                               .Select(m => m.message));
                        if (!r.Success || r.ShaderData == null || r.ShaderData.Length == 0)
                        {
                            errors.Add($"{plat} {ext}: {(msgs.Length > 0 ? msgs : "compilation failed")}");
                            continue;
                        }
                        var fn = $"{v.id}_{plat}_{ext}.bin";
                        File.WriteAllBytes(Path.Combine(cfg.@out, fn), r.ShaderData);
                        files.Add($"{{\"platform\": {Str(plat)}, \"stage\": {Str(ext)}, \"file\": {Str(fn)}}}");
                    }
                }
                if (v.pass != pass.Name && !(v.pass.StartsWith("<Unnamed Pass") && string.IsNullOrEmpty(pass.Name)))
                    errors.Add($"pass {v.pass_index} is named '{pass.Name}', the snapshot says '{v.pass}'");
            }
            catch (Exception e)
            {
                errors.Add((e.InnerException ?? e).Message);
            }
            lines.Add($"{{\"id\": {v.id}, \"files\": [{string.Join(", ", files)}], " +
                      $"\"fingerprint\": {Str(fingerprint)}, \"errors\": [{string.Join(", ", errors.Select(Str))}]}}");
        }
        var json = "{\"unity\": " + Str(Application.unityVersion) + ", \"variants\": [\n " + string.Join(",\n ", lines) + "]}\n";
        File.WriteAllText(Path.Combine(cfg.@out, "variants_result.json"), json);
        return "ok " + variants.Count;
    }

    [Serializable]
    class FingerprintConfig
    {
        public string[] shaders = new string[0];
        public string @out = "";
    }

    public static string Fingerprints(string configPath)
    {
        var cfg = JsonUtility.FromJson<FingerprintConfig>(File.ReadAllText(configPath));
        var byName = new Dictionary<string, Shader>();
        var items = cfg.shaders.Distinct().Select(name =>
        {
            var shader = Find(name, byName);
            return $"{Str(name)}: {Str(shader == null ? null : Fingerprint(shader))}";
        });
        File.WriteAllText(cfg.@out, "{\"unity\": " + Str(Application.unityVersion) + ", \"shaders\": {\n " +
                                    string.Join(",\n ", items) + "}}\n");
        return "ok " + cfg.shaders.Length;
    }

    // What the compiled code of a shader depends on: the Unity version, the Android platform defines and the color
    // space, and for an asset (project or package) its dependency hash (the file and everything it imports:
    // includes, subgraphs); built-in shaders change only with Unity.
    static string Fingerprint(Shader shader)
    {
        var path = AssetDatabase.GetAssetPath(shader);
        var asset = string.IsNullOrEmpty(path) || path.StartsWith("Resources/") || path.StartsWith("Library/")
            ? "builtin"
            : path + "#" + AssetDatabase.GetAssetDependencyHash(path);
        return string.Join("|", Application.unityVersion, PlayerSettings.colorSpace.ToString(),
                           PlatformDefines(ShaderCompilerPlatform.GLES3x), PlatformDefines(ShaderCompilerPlatform.Vulkan), asset);
    }

    // ShaderUtil.GetShaderPlatformKeywordsForBuildTarget(platform, Android): the BuiltinShaderDefines the compiler
    // gets for Android (normal map encoding, reversed Z, ...); through reflection, empty if the API is not there
    static string PlatformDefines(ShaderCompilerPlatform platform)
    {
        try
        {
            var m = typeof(ShaderUtil).GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
                .FirstOrDefault(x => x.Name == "GetShaderPlatformKeywordsForBuildTarget" && x.GetParameters().Length >= 2);
            if (m == null)
                return "";
            var args = m.GetParameters().Select((p, i) => i == 0 ? (object)platform : i == 1 ? BuildTarget.Android
                                                         : p.HasDefaultValue ? p.DefaultValue : null).ToArray();
            return m.Invoke(null, args) is Array a ? string.Join(",", a.Cast<object>().Select(o => o.ToString()).OrderBy(x => x)) : "";
        }
        catch (Exception)
        {
            return "";
        }
    }

    // Shader.Find by name; shaders that are not loaded yet are found among the project and package assets
    static Shader Find(string name, Dictionary<string, Shader> cache)
    {
        if (cache.TryGetValue(name, out var s))
            return s;
        s = Shader.Find(name);
        if (s == null)
        {
            foreach (var guid in AssetDatabase.FindAssets("t:Shader"))
            {
                foreach (var x in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(guid)).OfType<Shader>())
                    if (x.name == name)
                        s = x;
                if (s != null)
                    break;
            }
        }
        cache[name] = s;
        return s;
    }

    // JsonUtility does not serialize lists of this script's own types in a run_script assembly: JSON by hand
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
