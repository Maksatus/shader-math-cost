#version 310 es
precision highp float;
layout(std140, binding=0) uniform U { mat4 uMVP; vec4 uParams; };
layout(location=0) in vec4 aPos;
layout(location=1) in vec2 aUV;
layout(location=0) out vec2 vUV;
layout(location=1) out vec4 vExtra;
void main() {
  vec4 p = aPos;
  p.y += sin(p.x * 1.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 2.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 3.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 4.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 5.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 6.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 7.0 + p.z + uParams.x) * uParams.y;
  p.y += sin(p.x * 8.0 + p.z + uParams.x) * uParams.y;
  gl_Position = uMVP * p;
  vUV = aUV;
  vExtra = aPos;
}
