// Screen coverage of everything the game camera draws (plan item K1.3).
//
// Used by `python -m shaderopt frustum`:
//   unity command run_script --file ShaderoptFrustum.cs --entry ShaderoptFrustum.Run --args '["<config.json>"]'
// Runs at once in the open editor (Edit or Play Mode), no Frame Debugger:
//   1. renderers of the camera: enabled, active, in its culling mask, inside the frustum, in the active LOD;
//      `rendered` tells whether Unity drew it in the last frame (occlusion culling and the like);
//   2. "raster": every renderer x submesh is drawn with a counting shader with no depth test — the shader
//      atomically adds 1 per fragment to a buffer slot of that draw (everything in the frustum, with overdraw
//      and self-overlap). Alpha-tested materials clip with their texture alpha and threshold;
//   3. a depth pass of the opaque renderers, then "visible": the same count with the depth test.
// The cull mode of a shader is not readable through the API: materials without a _Cull property are counted
// with Back and with Front culling and the larger is taken (closed meshes give about the same; one-sided
// geometry facing away from the camera is drawn by its shader with Cull Off or Front).
// Vertex animation done in the real shader (VAT, wind, waves) is not reproduced: the mesh is drawn as is
// (skinned meshes are skinned). Result: <out>/frustum.json.
//
// config.json: {"out": "<absolute folder>", "camera": "", "light_modes": []}
//   camera: empty = Camera.main or the first game camera; light_modes: see Config

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public static class ShaderoptFrustum
{
    [Serializable]
    class Config
    {
        public string @out = "";
        public string camera = "";
        // LightModes the camera draws into color (from a Frame Debugger snapshot); empty = any *Forward*,
        // SRPDefaultUnlit or untagged pass
        public string[] light_modes = new string[0];
    }

    const string CountShader = @"Shader ""Hidden/ShaderoptCoverage"" {
CGINCLUDE
#include ""UnityCG.cginc""
#pragma target 4.5
sampler2D _ClipTex;
float4 _ClipTex_ST;
float _Cutoff;
float _UseClip;
struct appdata { float4 vertex : POSITION; float2 uv : TEXCOORD0; };
struct v2f { float4 pos : SV_POSITION; float2 uv : TEXCOORD0; };
v2f vert(appdata v) { v2f o; o.pos = UnityObjectToClipPos(v.vertex); o.uv = TRANSFORM_TEX(v.uv, _ClipTex); return o; }
void doclip(v2f i) { if (_UseClip > 0.5) clip(tex2D(_ClipTex, i.uv).a - _Cutoff); }
ENDCG
SubShader {
    Pass { // 0: depth of opaque renderers
        ZWrite On ZTest LEqual ColorMask 0 Cull [_CullMode]
        CGPROGRAM
        #pragma vertex vert
        #pragma fragment frag
        float4 frag(v2f i) : SV_Target { doclip(i); return 0; }
        ENDCG
    }
    Pass { // 1: count fragments of one draw into _Counts[_Slot]
        ZWrite Off ZTest [_ZTestMode] ColorMask 0 Cull [_CullMode]
        CGPROGRAM
        #pragma vertex vert
        #pragma fragment frag
        RWStructuredBuffer<uint> _Counts : register(u1);
        float _Slot;
        [earlydepthstencil]
        float4 frag(v2f i) : SV_Target { doclip(i); InterlockedAdd(_Counts[(uint)_Slot], 1); return 0; }
        ENDCG
    }
} }";

    class Item
    {
        public Renderer r;
        public int sub, lod = -1;
        public Material mat;
        public bool opaque, clip, cullKnown;
        public CullMode cull = CullMode.Back;
        public Texture clipTex;
        public Vector4 clipST = new Vector4(1, 1, 0, 0);
        public float cutoff = 0.5f;
        public bool depthUnreliable;
        public uint rasterBack, rasterFront, raster, visible;
    }

    public static string Run(string configPath)
    {
        var cfg = JsonUtility.FromJson<Config>(File.ReadAllText(configPath));
        Directory.CreateDirectory(cfg.@out);
        var cam = string.IsNullOrEmpty(cfg.camera)
            ? Camera.main ?? Camera.allCameras.FirstOrDefault(c => c.cameraType == CameraType.Game && c.targetTexture == null)
            : Camera.allCameras.FirstOrDefault(c => c.name == cfg.camera);
        if (cam == null)
            return Fail(cfg.@out, "no game camera found (set \"camera\")");
        int w = cam.pixelWidth, h = cam.pixelHeight;
        var items = Collect(cam, cfg.light_modes ?? new string[0]);

        var sh = ShaderUtil.CreateShaderAsset(CountShader, false);  // in memory, nothing is written to the project
        if (sh == null || !sh.isSupported)
            return Fail(cfg.@out, "coverage shader does not compile");
        var mat = new Material(sh) { hideFlags = HideFlags.HideAndDontSave };
        var color = new RenderTexture(w, h, 0, RenderTextureFormat.R8) { hideFlags = HideFlags.HideAndDontSave };
        var depth = new RenderTexture(w, h, 32, RenderTextureFormat.Depth) { hideFlags = HideFlags.HideAndDontSave };
        color.Create(); depth.Create();
        int n = Math.Max(1, items.Count * 2);
        var counts = new ComputeBuffer(n, sizeof(uint));
        try
        {
            // raster with no depth test: slot 2i = Back (or the material's cull), 2i+1 = Front
            var c = Execute(cam, color, depth, counts, n, cmd =>
            {
                for (int i = 0; i < items.Count; i++)
                {
                    var it = items[i];
                    Draw(cmd, it, mat, 1, 2 * i, CompareFunction.Always, it.cullKnown ? it.cull : CullMode.Back);
                    if (!it.cullKnown)
                        Draw(cmd, it, mat, 1, 2 * i + 1, CompareFunction.Always, CullMode.Front);
                }
            });
            foreach (var (it, i) in items.Select((x, i) => (x, i)))
            {
                it.rasterBack = c[2 * i];
                it.rasterFront = it.cullKnown ? 0 : c[2 * i + 1];
                if (!it.cullKnown && it.rasterFront > it.rasterBack) { it.cull = CullMode.Front; it.depthUnreliable = true; }
                it.raster = Math.Max(it.rasterBack, it.rasterFront);
            }
            // depth of the opaque renderers, then the visible count: slot 2i
            c = Execute(cam, color, depth, counts, n, cmd =>
            {
                // geometry that only shows with Front culling is transformed by its shader in a way the plain
                // counting shader does not repeat (its depth lands elsewhere): it is counted but does not occlude
                foreach (var it in items.Where(x => x.opaque && !x.depthUnreliable))
                    Draw(cmd, it, mat, 0, 0, CompareFunction.LessEqual, it.cull);
                for (int i = 0; i < items.Count; i++)
                    Draw(cmd, items[i], mat, 1, 2 * i, CompareFunction.LessEqual, items[i].cull);
            });
            for (int i = 0; i < items.Count; i++)
                items[i].visible = c[2 * i];
            File.WriteAllText(Path.Combine(cfg.@out, "frustum.json"), Json(cam, w, h, items));
            return $"ok {items.Count}";
        }
        finally
        {
            counts.Release();
            color.Release(); depth.Release();
            UnityEngine.Object.DestroyImmediate(color); UnityEngine.Object.DestroyImmediate(depth);
            UnityEngine.Object.DestroyImmediate(mat); UnityEngine.Object.DestroyImmediate(sh);
        }
    }

    static uint[] Execute(Camera cam, RenderTexture color, RenderTexture depth, ComputeBuffer counts, int n,
                          Action<CommandBuffer> draws)
    {
        counts.SetData(new uint[n]);
        var cmd = new CommandBuffer { name = "shaderopt coverage" };
        cmd.SetRenderTarget(color, depth);
        cmd.ClearRenderTarget(true, true, Color.clear);
        cmd.SetViewProjectionMatrices(cam.worldToCameraMatrix, GL.GetGPUProjectionMatrix(cam.projectionMatrix, true));
        cmd.SetRandomWriteTarget(1, counts);
        draws(cmd);
        cmd.ClearRandomWriteTargets();
        Graphics.ExecuteCommandBuffer(cmd);
        cmd.Release();
        var result = new uint[n];
        counts.GetData(result);
        return result;
    }

    // DrawRenderer takes no property block: per-draw values go through globals
    static void Draw(CommandBuffer cmd, Item it, Material mat, int pass, int slot, CompareFunction ztest, CullMode cull)
    {
        cmd.SetGlobalFloat("_Slot", slot);
        cmd.SetGlobalFloat("_ZTestMode", (float)ztest);
        cmd.SetGlobalFloat("_CullMode", (float)cull);
        cmd.SetGlobalFloat("_UseClip", it.clip ? 1 : 0);
        if (it.clip)
        {
            cmd.SetGlobalTexture("_ClipTex", it.clipTex != null ? it.clipTex : Texture2D.whiteTexture);
            cmd.SetGlobalVector("_ClipTex_ST", it.clipST);
            cmd.SetGlobalFloat("_Cutoff", it.cutoff);
        }
        cmd.DrawRenderer(it.r, mat, it.sub, pass);
    }

    static List<Item> Collect(Camera cam, string[] lightModes)
    {
        var planes = GeometryUtility.CalculateFrustumPlanes(cam);
        var activeLod = ActiveLods(cam);
        var items = new List<Item>();
        foreach (var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
        {
            if (!r.enabled || !r.gameObject.activeInHierarchy || r.forceRenderingOff) continue;
            if (r.shadowCastingMode == ShadowCastingMode.ShadowsOnly) continue;
            if ((cam.cullingMask & (1 << r.gameObject.layer)) == 0) continue;
            if (!(r is MeshRenderer || r is SkinnedMeshRenderer || r is ParticleSystemRenderer || r is BillboardRenderer)) continue;
            if (!GeometryUtility.TestPlanesAABB(planes, r.bounds)) continue;
            int lod = -1;
            if (activeLod.TryGetValue(r, out var l))
            {
                if (l.Item1 != l.Item2) continue;  // renderer of a LOD that is not active
                lod = l.Item1;
            }
            int subs = r is MeshRenderer ? (r.GetComponent<MeshFilter>()?.sharedMesh?.subMeshCount ?? 0)
                     : r is SkinnedMeshRenderer smr ? (smr.sharedMesh?.subMeshCount ?? 0) : 1;
            var mats = r.sharedMaterials;
            for (int i = 0; i < mats.Length; i++)
            {
                var m = mats[i];
                if (m == null || m.shader == null || !DrawnByCamera(m.shader, lightModes)) continue;
                var it = new Item { r = r, sub = subs > 0 ? Math.Min(i, subs - 1) : 0, mat = m, opaque = m.renderQueue < 2500, lod = lod };
                if (m.HasProperty("_Cull")) { it.cull = (CullMode)(int)m.GetFloat("_Cull"); it.cullKnown = true; }
                else if (m.IsKeywordEnabled("_DOUBLESIDED")) { it.cull = CullMode.Off; it.cullKnown = true; }
                AlphaClip(it);
                items.Add(it);
            }
        }
        return items;
    }

    // a renderer counts only if its shader has a pass the camera draws into color: shadow-only, depth, meta
    // and custom passes (e.g. a shadow proxy) are not part of the camera image
    static bool DrawnByCamera(Shader s, string[] lightModes)
    {
        var tag = new ShaderTagId("LightMode");
        for (int p = 0; p < s.passCount; p++)
        {
            var lm = s.FindPassTagValue(p, tag).name ?? "";
            if (lightModes.Length > 0 ? lightModes.Contains(lm) || (lm == "" && lightModes.Contains("SRPDefaultUnlit"))
                : lm == "" || lm == "SRPDefaultUnlit" || lm.IndexOf("Forward", StringComparison.OrdinalIgnoreCase) >= 0)
                return true;
        }
        return false;
    }

    // alpha test: by keyword or the AlphaTest queue; threshold = a float property named like a cutoff or a clip
    // threshold, texture = _BaseMap / _MainTex or the first 2D texture of the shader
    static void AlphaClip(Item it)
    {
        var m = it.mat;
        bool kw = m.shaderKeywords.Any(k => k.Contains("ALPHATEST") || k.Contains("ALPHACLIP") || k.Contains("CUTOUT"));
        if (!kw && !(m.renderQueue >= 2450 && m.renderQueue < 2500)) return;
        var s = m.shader;
        string cutoff = null, tex = null;
        for (int i = 0; i < s.GetPropertyCount(); i++)
        {
            var name = s.GetPropertyName(i);
            var low = name.ToLowerInvariant();
            var type = s.GetPropertyType(i);
            if (cutoff == null && (type == ShaderPropertyType.Float || type == ShaderPropertyType.Range)
                && (low.Contains("cutoff") || (low.Contains("clip") && (low.Contains("thres") || low.Contains("tresh")))))
                cutoff = name;
            if (tex == null && type == ShaderPropertyType.Texture && s.GetPropertyTextureDimension(i) == TextureDimension.Tex2D)
                tex = name;
        }
        if (m.HasProperty("_BaseMap")) tex = "_BaseMap";
        else if (m.HasProperty("_MainTex")) tex = "_MainTex";
        if (tex == null || m.GetTexture(tex) == null) return;
        it.clip = true;
        it.clipTex = m.GetTexture(tex);
        it.clipST = m.HasProperty(tex + "_ST") ? m.GetVector(tex + "_ST") : new Vector4(1, 1, 0, 0);
        it.cutoff = cutoff != null ? m.GetFloat(cutoff) : 0.5f;
    }

    // renderer -> (LOD index it belongs to, active LOD index of its group; -1 = the group is culled)
    static Dictionary<Renderer, (int, int)> ActiveLods(Camera cam)
    {
        var res = new Dictionary<Renderer, (int, int)>();
        foreach (var g in UnityEngine.Object.FindObjectsByType<LODGroup>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
        {
            if (!g.enabled) continue;
            var lods = g.GetLODs();
            var t = g.transform;
            var s = t.lossyScale;
            float size = g.size * Mathf.Max(Mathf.Abs(s.x), Mathf.Abs(s.y), Mathf.Abs(s.z));
            float dist = Vector3.Distance(cam.transform.position, t.TransformPoint(g.localReferencePoint));
            float rel = cam.orthographic ? size / (2f * cam.orthographicSize)
                      : size / (2f * Mathf.Max(dist, 1e-4f) * Mathf.Tan(0.5f * cam.fieldOfView * Mathf.Deg2Rad));
            rel *= QualitySettings.lodBias;
            int active = -1;
            for (int i = QualitySettings.maximumLODLevel; i < lods.Length; i++)
                if (rel >= lods[i].screenRelativeTransitionHeight) { active = i; break; }
            for (int i = 0; i < lods.Length; i++)
                foreach (var r in lods[i].renderers)
                    if (r != null && !res.ContainsKey(r)) res[r] = (i, active);
        }
        return res;
    }

    static string Json(Camera cam, int w, int h, List<Item> items)
    {
        var sb = new StringBuilder();
        sb.Append("{\"unity\": ").Append(Str(Application.unityVersion))
          .Append(", \"camera\": ").Append(Str(cam.name))
          .Append(", \"width\": ").Append(w).Append(", \"height\": ").Append(h)
          .Append(", \"play_mode\": ").Append(EditorApplication.isPlaying ? "true" : "false")
          .Append(", \"quality\": ").Append(Str(QualitySettings.names[QualitySettings.GetQualityLevel()]))
          .Append(",\n \"items\": [\n");
        for (int i = 0; i < items.Count; i++)
        {
            var it = items[i];
            var mesh = it.r is MeshRenderer ? it.r.GetComponent<MeshFilter>()?.sharedMesh : (it.r as SkinnedMeshRenderer)?.sharedMesh;
            long verts = 0;
            if (mesh != null)
                verts = mesh.subMeshCount > it.sub ? mesh.GetSubMesh(it.sub).vertexCount : mesh.vertexCount;
            if (i > 0) sb.Append(",\n");
            sb.Append("  {\"renderer\": ").Append(Str(HierarchyPath(it.r.transform)))
              .Append(", \"type\": ").Append(Str(it.r.GetType().Name))
              .Append(", \"mesh\": ").Append(Str(mesh != null ? mesh.name : null))
              .Append(", \"submesh\": ").Append(it.sub)
              .Append(", \"material\": ").Append(Str(it.mat.name))
              .Append(", \"shader\": ").Append(Str(it.mat.shader.name))
              .Append(", \"keywords\": [").Append(string.Join(", ", it.mat.shaderKeywords.OrderBy(k => k).Select(Str))).Append("]")
              .Append(", \"queue\": ").Append(it.mat.renderQueue)
              .Append(", \"opaque\": ").Append(it.opaque ? "true" : "false")
              .Append(", \"alpha_clip\": ").Append(it.clip ? "true" : "false")
              .Append(", \"cull\": ").Append(Str(it.cull.ToString()))
              .Append(", \"cull_known\": ").Append(it.cullKnown ? "true" : "false")
              .Append(", \"occluder\": ").Append(it.opaque && !it.depthUnreliable ? "true" : "false")
              .Append(", \"lod\": ").Append(it.lod)
              .Append(", \"vertices\": ").Append(verts)
              .Append(", \"raster_px\": ").Append(it.raster)
              .Append(", \"raster_back_px\": ").Append(it.rasterBack)
              .Append(", \"raster_front_px\": ").Append(it.rasterFront)
              .Append(", \"visible_px\": ").Append(it.visible)
              .Append(", \"rendered\": ").Append(it.r.isVisible ? "true" : "false")
              .Append("}");
        }
        sb.Append("\n]}\n");
        return sb.ToString();
    }

    static Mesh Mesh(Item it) => it.r is MeshRenderer ? it.r.GetComponent<MeshFilter>()?.sharedMesh : (it.r as SkinnedMeshRenderer)?.sharedMesh;

    static string HierarchyPath(Transform t)
    {
        var parts = new List<string>();
        for (; t != null; t = t.parent) parts.Add(t.name);
        parts.Reverse();
        return string.Join("/", parts);
    }

    static string Fail(string outDir, string msg)
    {
        File.WriteAllText(Path.Combine(outDir, "frustum.error"), msg);
        return "error: " + msg;
    }

    static string Str(string v)
    {
        if (v == null) return "null";
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
