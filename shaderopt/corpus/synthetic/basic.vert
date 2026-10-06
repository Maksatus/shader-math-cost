#version 310 es
precision highp float;
layout(std140, binding=0) uniform U { mat4 uMVP; vec4 uParams; };
layout(location=0) in vec4 aPos;
layout(location=1) in vec2 aUV;
layout(location=0) out vec2 vUV;
layout(location=1) out vec4 vExtra;
void main() {
  gl_Position = uMVP * aPos;
  vUV = aUV;
  vExtra = aPos;
}
