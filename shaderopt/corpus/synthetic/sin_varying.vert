#version 310 es
precision highp float;
layout(std140, binding=0) uniform U { mat4 uMVP; vec4 uParams; };
layout(location=0) in vec4 aPos;
layout(location=1) in vec2 aUV;
layout(location=0) out vec2 vUV;
layout(location=1) out vec4 vExtra;
void main() {
  gl_Position = uMVP * aPos;
  vec4 e = aPos;
  e = sin(e * 1.0 + aUV.xyxy + uParams);
  e = sin(e * 2.0 + aUV.xyxy + uParams);
  e = sin(e * 3.0 + aUV.xyxy + uParams);
  e = sin(e * 4.0 + aUV.xyxy + uParams);
  e = sin(e * 5.0 + aUV.xyxy + uParams);
  e = sin(e * 6.0 + aUV.xyxy + uParams);
  e = sin(e * 7.0 + aUV.xyxy + uParams);
  e = sin(e * 8.0 + aUV.xyxy + uParams);
  vUV = aUV;
  vExtra = e;
}
