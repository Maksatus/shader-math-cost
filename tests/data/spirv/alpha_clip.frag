#version 310 es
// An alpha-clipped fragment shader (rd_spirv.without_discard): a discard in main and one in a function it calls.
precision highp float;
layout(location = 0) in highp vec4 vX;
layout(location = 0) out highp vec4 o;
void clipBelow(float a) { if (a < 0.25) { discard; } }
void main() {
  if (vX.w < 0.5) { discard; }
  clipBelow(vX.z);
  o = vX;
}
