#version 310 es
precision highp float;
layout(std140, binding=0) uniform U { int uCount; };
layout(location=0) in highp vec4 vX;
layout(location=0) out highp vec4 o;
void main() {
  vec4 x = vX;
  for (int i = 0; i < uCount; i++) { x = sin(x) + vX; }
  o = x;
}
