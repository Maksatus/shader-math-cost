#version 310 es
precision highp float;
layout(std140, binding=0) uniform U { highp mat4 uM4; highp mat3 uM3; };
layout(location=0) in highp vec4 vX;
layout(location=1) in highp vec4 vP0;
layout(location=2) in highp vec4 vP1;
layout(location=3) in highp vec4 vP2;
layout(location=4) in highp vec4 vP3;
layout(location=5) in highp vec4 vP4;
layout(location=6) in highp vec4 vP5;
layout(location=7) in highp vec4 vP6;
layout(location=8) in highp vec4 vP7;
layout(location=0) out highp vec4 o;
void main() {
  highp float x = vX.x;
  x = (acos(x)) + vP0.x;
  x = (acos(x)) + vP1.x;
  x = (acos(x)) + vP2.x;
  x = (acos(x)) + vP3.x;
  x = (acos(x)) + vP4.x;
  x = (acos(x)) + vP5.x;
  x = (acos(x)) + vP6.x;
  x = (acos(x)) + vP7.x;
  o = vec4(x);
}
