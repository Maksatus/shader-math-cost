#version 310 es
// Forward+ loops as HLSLcc writes them (core/loops): u_xlatu1.xw is written as load -> OpVectorShuffle -> store of the
// whole vector, the directional loop bound min(CR_Params0.w, 128) is read from its .w; the light loop is while(true)
// with a nested word scan and its exit test in the body; the wave loop reads a uniform array by its counter (malioc
// unrolls it and hoists the reads at a few constant iterations).
precision highp float;
precision highp int;
layout(std140, set = 1, binding = 0) uniform PGlobals { vec4 CR_Params0; uvec4 _WaveCount; vec4 _Waves[32]; };
layout(std430, set = 0, binding = 3) readonly buffer Tiles { uint CR_Tiles[]; };
layout(location = 0) in highp vec4 vX;
layout(location = 0) out highp vec4 o;
uvec4 u_xlatu1;
uint u_xlatu2;
uint u_xlatu3;
int u_xlati4;
vec4 u_xlat5;
bool u_xlatb6;
void main() {
  u_xlat5 = vX;
  u_xlatu1.y = 7u;
  u_xlatu1.xw = uvec2(CR_Params0.zw);
  u_xlatu1.x = min(u_xlatu1.w, 128u);
  for (uint i = 0u; i < u_xlatu1.x; i++) { u_xlat5 = sin(u_xlat5) + vX; }
  u_xlatu2 = uint(vX.z * 64.0);
  u_xlatu3 = 0u;
  while (true) {
    u_xlati4 = int(CR_Tiles[u_xlatu2]);
    while (true) {
      u_xlatb6 = u_xlati4 == 0 && u_xlatu2 < 64u;
      if (!u_xlatb6) { break; }
      u_xlatu2 = u_xlatu2 + 1u;
      u_xlati4 = int(CR_Tiles[u_xlatu2]);
    }
    if (u_xlati4 == 0) { break; }
    u_xlatu3 = uint(findLSB(u_xlati4));
    u_xlat5 = cos(u_xlat5 * float(u_xlatu3)) + vX.yzwx;
    u_xlatu2 = u_xlatu2 + 1u;
  }
  for (uint j = 0u; j < _WaveCount.x; j++) {
    u_xlat5.x += sin(dot(vX.xy, _Waves[j].xy) + _Waves[j].z) * _Waves[j].w;
  }
  o = u_xlat5;
}
