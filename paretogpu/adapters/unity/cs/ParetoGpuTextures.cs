// Android formats of the project's textures by name: {"names"} -> {"unity", "textures": {name: [{"path", "format"}]
// or []}}; format = the importer's Android override, else its automatic Android format. The editor itself
// samples ASTC textures decoded (RGBA8), so a PC capture does not show them.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class ParetoGpuTextures
{
    [Serializable]
    class Config
    {
        public string[] names = new string[0];
    }

    public static string Formats(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(ParetoGpuJob.Open(configPath));
        var items = new List<string>();
        foreach (var name in cfg.names.Distinct())
        {
            var found = new List<string>();
            foreach (var guid in AssetDatabase.FindAssets($"{name} t:Texture"))
            {
                var path = AssetDatabase.GUIDToAssetPath(guid);
                if (Path.GetFileNameWithoutExtension(path) != name || !(AssetImporter.GetAtPath(path) is TextureImporter imp))
                    continue;
                var android = imp.GetPlatformTextureSettings("Android");
                var format = android.overridden ? android.format : imp.GetAutomaticFormat("Android");
                found.Add($"{{\"path\": {Str(path)}, \"format\": {Str(format.ToString())}}}");
            }
            items.Add($"{Str(name)}: [{string.Join(", ", found)}]");
        }
        return ParetoGpuJob.Done("{\"unity\": " + Str(Application.unityVersion) + ", \"textures\": {\n " +
                                 string.Join(",\n ", items) + "}}\n");
    }

    static string Str(string v) => ParetoGpuJob.Str(v);
}
