"""Candidates for moving to the vertex shader (project/vertexmove.py, plan A3.4) on synthetic shaders.

The shaders are written the way Unity (HLSLcc) prints Shader Graph's nodes: Rotate (radians), Tiling And Offset,
Flipbook. Run: python -m unittest paretogpu.project.test_vertexmove   (from the repository root)
"""
import unittest

from paretogpu.project import vertexmove

HEAD = """#version 310 es
precision highp float;
uniform 	vec4 _BaseMap_ST;
uniform 	float _Rotation;
uniform 	vec2 _Center;
uniform 	vec4 _TimeParameters;
uniform 	float _Width;
UNITY_LOCATION(0) uniform mediump sampler2D _BaseMap;
UNITY_LOCATION(1) uniform mediump sampler2D _Noise;
layout(location = 0) in highp vec4 vs_TEXCOORD0;
layout(location = 1) in highp vec3 vs_NORMAL0;
layout(location = 0) out mediump vec4 SV_Target0;
vec4 u_xlat0;
vec2 u_xlat1;
float u_xlat2;
float u_xlat3;
mediump vec4 u_xlat16_4;
void main()
{
"""

# Rotate(UV, angle from a property): sin / cos of a uniform times (uv - center): affine in uv
ROTATE_PROPERTY = HEAD + """    u_xlat2 = sin(_Rotation);
    u_xlat3 = cos(_Rotation);
    u_xlat0.xy = vs_TEXCOORD0.xy + (-_Center.xy);
    u_xlat1.x = u_xlat0.x * u_xlat3 + (-u_xlat0.y * u_xlat2);
    u_xlat1.y = u_xlat0.x * u_xlat2 + u_xlat0.y * u_xlat3;
    u_xlat1.xy = u_xlat1.xy + _Center.xy;
    u_xlat16_4 = texture(_BaseMap, u_xlat1.xy);
    SV_Target0 = u_xlat16_4;
    return;
}
"""

# Rotate(UV, angle from a noise texture): the angle depends on the pixel: not affine
ROTATE_NOISE = HEAD + """    u_xlat2 = texture(_Noise, vs_TEXCOORD0.xy).x;
    u_xlat3 = cos(u_xlat2);
    u_xlat2 = sin(u_xlat2);
    u_xlat0.xy = vs_TEXCOORD0.xy + (-_Center.xy);
    u_xlat1.x = u_xlat0.x * u_xlat3 + (-u_xlat0.y * u_xlat2);
    u_xlat1.y = u_xlat0.x * u_xlat2 + u_xlat0.y * u_xlat3;
    u_xlat1.xy = u_xlat1.xy + _Center.xy;
    u_xlat16_4 = texture(_BaseMap, u_xlat1.xy);
    SV_Target0 = u_xlat16_4;
    return;
}
"""

TILING_AND_NORMAL = HEAD + """    u_xlat0.xy = vs_TEXCOORD0.xy * _BaseMap_ST.xy + _BaseMap_ST.zw;
    u_xlat16_4 = texture(_BaseMap, u_xlat0.xy);
    u_xlat2 = dot(vs_NORMAL0.xyz, vs_NORMAL0.xyz);
    u_xlat2 = inversesqrt(u_xlat2);
    u_xlat0.xyz = vec3(u_xlat2) * vs_NORMAL0.xyz;
    SV_Target0.xyz = u_xlat16_4.xyz * u_xlat0.xyz;
    SV_Target0.w = 1.0;
    return;
}
"""

FLIPBOOK = HEAD + """    u_xlat2 = _TimeParameters.x * 8.0;
    u_xlat2 = floor(u_xlat2);
    u_xlat3 = u_xlat2 / _Width;
    u_xlat3 = floor(u_xlat3);
    u_xlat2 = (-_Width) * u_xlat3 + u_xlat2;
    u_xlat1.xy = vec2(u_xlat2, u_xlat3) + vs_TEXCOORD0.xy;
    u_xlat1.xy = u_xlat1.xy / vec2(_Width);
    u_xlat16_4 = texture(_BaseMap, u_xlat1.xy);
    SV_Target0 = u_xlat16_4;
    return;
}
"""


class Candidates(unittest.TestCase):
    def test_rotate_by_property_is_a_candidate(self):
        """Ready-when of A3.4: Rotate(UV, angle from a property) is marked..."""
        c = vertexmove.candidates(ROTATE_PROPERTY)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["text"], "u_xlat1.xy = u_xlat1.xy + _Center.xy;")
        self.assertIn("поворот", c[0]["label"])
        self.assertEqual(c[0]["varyings"], ["vs_TEXCOORD0"])
        self.assertEqual(len(c[0]["chain"]), 6)  # sin, cos and the rotation of (uv - center)

    def test_rotate_by_noise_is_not(self):
        """...and Rotate(UV, noise) is not."""
        self.assertEqual(vertexmove.candidates(ROTATE_NOISE), [])

    def test_tiling_yes_normalize_no(self):
        c = vertexmove.candidates(TILING_AND_NORMAL)
        self.assertEqual([x["text"] for x in c], ["u_xlat0.xy = vs_TEXCOORD0.xy * _BaseMap_ST.xy + _BaseMap_ST.zw;"])
        self.assertEqual(c[0]["label"], "Tiling / Offset")

    def test_flipbook_by_time(self):
        c = vertexmove.candidates(FLIPBOOK)
        self.assertEqual([x["text"] for x in c], ["u_xlat1.xy = u_xlat1.xy / vec2(_Width);"])
        self.assertIn("флипбук", c[0]["label"])

    def test_budget(self):
        self.assertEqual(vertexmove.budget(ROTATE_PROPERTY), {"varyings": 2, "free_components": 1})


if __name__ == "__main__":
    unittest.main()
