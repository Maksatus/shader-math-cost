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
  x = (sqrt(x)) + vP0.x;
  x = (sqrt(x)) + vP1.x;
  x = (sqrt(x)) + vP2.x;
  x = (sqrt(x)) + vP3.x;
  x = (sqrt(x)) + vP4.x;
  x = (sqrt(x)) + vP5.x;
  x = (sqrt(x)) + vP6.x;
  x = (sqrt(x)) + vP7.x;
  x = (sqrt(x)) + vP0.y;
  x = (sqrt(x)) + vP1.y;
  x = (sqrt(x)) + vP2.y;
  x = (sqrt(x)) + vP3.y;
  x = (sqrt(x)) + vP4.y;
  x = (sqrt(x)) + vP5.y;
  x = (sqrt(x)) + vP6.y;
  x = (sqrt(x)) + vP7.y;
  o = vec4(x);
}
