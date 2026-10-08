#version 310 es
precision mediump float;
layout(std140, binding=0) uniform U { mediump vec4 uColor; };
layout(binding=1) uniform mediump sampler2D uTex;
layout(location=0) in highp vec2 vUV;
layout(location=0) out mediump vec4 o;
void main() { o = texture(uTex, vUV) * uColor; }
