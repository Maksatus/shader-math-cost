#version 310 es
// The light loop of a Unity shader as HLSLcc writes it (core/loops.spirv_bounds): temporaries at module scope
// (Private in SPIR-V, stored and loaded), the count min(_AdditionalLightsCount.x, unity_LightData.y) of two uniform
// blocks made an int, then a uint; a second loop whose bound is stored in a branch has no bound.
precision highp float;
layout(std140, set = 1, binding = 0) uniform PGlobals { vec4 _Pad; vec4 _AdditionalLightsCount; };
layout(std140, set = 1, binding = 2) uniform UnityPerDraw { vec4 _M[11]; vec4 unity_LightData; };
layout(location = 0) in highp vec4 vX;
layout(location = 0) out highp vec4 o;
float u_xlat0;
uint u_xlatu1;
int u_xlati2;
vec4 u_xlat3;
void main() {
  u_xlat0 = min(_AdditionalLightsCount.x, unity_LightData.y);
  u_xlatu1 = uint(int(u_xlat0));
  u_xlat3 = vX;
  for (uint i = 0u; i < u_xlatu1; i++) { u_xlat3 = sin(u_xlat3) + vX; }
  if (vX.x > 0.5) { u_xlati2 = int(_Pad.y); } else { u_xlati2 = 3; }
  for (int j = 0; j < u_xlati2; j++) { u_xlat3 = cos(u_xlat3); }
  o = u_xlat3;
}
