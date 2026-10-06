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
  highp vec4 x = vX;
  x = (sin(x)) + vP0.xyzw;
  x = (sin(x)) + vP1.xyzw;
  x = (sin(x)) + vP2.xyzw;
  x = (sin(x)) + vP3.xyzw;
  x = (sin(x)) + vP4.xyzw;
  x = (sin(x)) + vP5.xyzw;
  x = (sin(x)) + vP6.xyzw;
  x = (sin(x)) + vP7.xyzw;
  x = (sin(x)) + vP0.yzwx;
  x = (sin(x)) + vP1.yzwx;
  x = (sin(x)) + vP2.yzwx;
  x = (sin(x)) + vP3.yzwx;
  x = (sin(x)) + vP4.yzwx;
  x = (sin(x)) + vP5.yzwx;
  x = (sin(x)) + vP6.yzwx;
  x = (sin(x)) + vP7.yzwx;
  o = x;
}
