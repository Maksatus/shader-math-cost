#version 310 es
// SSR-style ray march (core/loops): while(true) that leaves at a hit and also at a constant step count (24), so a
// forced loop stops growing past 24 iterations.
precision highp float;
precision highp int;
layout(set = 0, binding = 1) uniform highp sampler2D _Depth;
layout(location = 0) in highp vec4 vX;
layout(location = 0) out highp vec4 o;
int u_xlati0;
vec3 u_xlat1;
bool u_xlatb2;
void main() {
  u_xlat1 = vX.xyz;
  u_xlati0 = 0;
  while (true) {
    u_xlatb2 = float(u_xlati0) >= 24.0;
    if (u_xlatb2) { break; }
    u_xlat1 = u_xlat1 + vX.yzx * 0.1;
    if (textureLod(_Depth, u_xlat1.xy, 0.0).x < u_xlat1.z) { break; }
    u_xlati0 = u_xlati0 + 1;
  }
  o = vec4(u_xlat1, float(u_xlati0));
}
