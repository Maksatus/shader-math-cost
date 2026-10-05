"""Benchmarked functions.

kind -> GLSL type per variant size (dim 1 / 2 / 4, see glsl_type):
  "same"   - float / vec2 / vec4
  "geo"    - geometric: vec3 / vec2 / vec4
  "mat"    - matrix * vector: mat3*vec3 / mat2*vec2 / mat4*vec4
  "matmat" - matrix chain: mat3 / mat2 / mat4
  "int"    - int / ivec2 / ivec4;  "uint" - uint / uvec2 / uvec4
  "vec3", "fixed3", "fixed4" - always vec3 / vec3 / vec4 (cross, Unity helpers)
combine: chain step is (expr) + w_i; the w_i add is subtracted via baseline.
         False for the basic arithmetic ops which form a chain by themselves.
extra_adds: additional adds inside expr to subtract (e.g. sin(x)+cos(x)).

Expression: a format string, a (scalar, vector) pair, or a dict keyed by GLSL
type. Placeholders: {x} chain value, {a},{b} varying operands of type T,
{s} scalar varying operand, {T} GLSL type, {D} vector/matrix size,
{I} int vector of the same size.

Keep the expensive part of each function dependent on {x}: the operand pool
has only 32 distinct scalars, so anything computed from operands alone (e.g.
rcp(a) in x / a) gets reused across the chain and looks almost free.

Do not edit existing expressions without need: results are cached by shader
source, any change recompiles that function on every GPU.
"""

# (id, hlsl, glsl_expr, category, kind, combine, extra_adds, note)
FUNCS = [
    # --- arithmetic
    ("add",        "a + b",        "{x} + {a}",                 "Арифметика", "same", False, 0, ""),
    ("mul",        "a * b",        "{x} * {a}",                 "Арифметика", "same", False, 0, ""),
    ("mad",        "mad / a*b+c",  "{x} * {a} + {b}",           "Арифметика", "same", False, 0, "Сливается в одну FMA"),
    ("div",        "a / b",        "{a} / {x}",                 "Арифметика", "same", True,  0, "Деление на переменную (не константу)"),
    ("rcp",        "rcp",          "1.0 / {x}",                 "Арифметика", "same", True,  0, ""),
    ("abs",        "abs",          "abs({x})",                  "Арифметика", "same", True,  0, "Обычно бесплатный модификатор операнда"),
    ("sign",       "sign",         "sign({x})",                 "Арифметика", "same", True,  0, ""),
    ("min",        "min",          "min({x}, {a})",             "Арифметика", "same", True,  0, ""),
    ("max",        "max",          "max({x}, {a})",             "Арифметика", "same", True,  0, ""),
    ("clamp",      "clamp",        "clamp({x}, {a}, {b})",      "Арифметика", "same", True,  0, "Границы — переменные"),
    ("saturate",   "saturate",     "clamp({x}, 0.0, 1.0)",      "Арифметика", "same", True,  0, "Обычно бесплатный модификатор результата"),
    ("lerp",       "lerp",         "mix({x}, {a}, {b})",        "Арифметика", "same", True,  0, ""),
    ("step",       "step",         "step({a}, {x})",            "Арифметика", "same", True,  0, ""),
    ("smoothstep", "smoothstep",   "smoothstep({a}, {x}, {b})", "Арифметика", "same", True,  0, "Границы — переменные"),
    # --- rounding
    ("floor",      "floor",        "floor({x})",                "Округление", "same", True,  0, ""),
    ("ceil",       "ceil",         "ceil({x})",                 "Округление", "same", True,  0, ""),
    ("round",      "round",        "round({x})",                "Округление", "same", True,  0, ""),
    ("trunc",      "trunc",        "trunc({x})",                "Округление", "same", True,  0, ""),
    ("frac",       "frac",         "fract({x})",                "Округление", "same", True,  0, ""),
    ("fmod",       "fmod",         "{a} - {x} * trunc({a} / {x})", "Округление", "same", True, 0, "x - y*trunc(x/y), семантика HLSL fmod"),
    ("mod",        "mod (GLSL)",   "mod({a}, {x})",             "Округление", "same", True,  0, "Встроенной в HLSL нет, пишется формулой. Отличается от fmod для отрицательных x"),
    # --- exp / pow
    ("sqrt",       "sqrt",         "sqrt({x})",                 "Степени и логарифмы", "same", True, 0, ""),
    ("rsqrt",      "rsqrt",        "inversesqrt({x})",          "Степени и логарифмы", "same", True, 0, ""),
    ("exp2",       "exp2",         "exp2({x})",                 "Степени и логарифмы", "same", True, 0, ""),
    ("exp",        "exp",          "exp({x})",                  "Степени и логарифмы", "same", True, 0, ""),
    ("log2",       "log2",         "log2({x})",                 "Степени и логарифмы", "same", True, 0, ""),
    ("log",        "log",          "log({x})",                  "Степени и логарифмы", "same", True, 0, ""),
    ("pow",        "pow",          "pow({x}, {a})",             "Степени и логарифмы", "same", True, 0, "Степень — переменная (pow с константой 2/3 и т.п. дешевле)"),
    # --- trig
    ("sin",        "sin",          "sin({x})",                  "Тригонометрия", "same", True, 0, ""),
    ("cos",        "cos",          "cos({x})",                  "Тригонометрия", "same", True, 0, ""),
    ("sincos",     "sincos",       "sin({x}) + cos({x})",       "Тригонометрия", "same", True, 1, "sin и cos от одного аргумента"),
    ("tan",        "tan",          "tan({x})",                  "Тригонометрия", "same", True, 0, ""),
    ("asin",       "asin",         "asin({x})",                 "Тригонометрия", "same", True, 0, ""),
    ("acos",       "acos",         "acos({x})",                 "Тригонометрия", "same", True, 0, ""),
    ("atan",       "atan",         "atan({x})",                 "Тригонометрия", "same", True, 0, ""),
    ("atan2",      "atan2",        "atan({x}, {a})",            "Тригонометрия", "same", True, 0, ""),
    ("radians",    "radians",      "radians({x})",              "Тригонометрия", "same", True, 0, "Умножение на константу"),
    ("degrees",    "degrees",      "degrees({x})",              "Тригонометрия", "same", True, 0, "Умножение на константу"),
    # --- hyperbolic
    ("sinh",       "sinh",         "sinh({x})",                 "Гиперболические", "same", True, 0, ""),
    ("cosh",       "cosh",         "cosh({x})",                 "Гиперболические", "same", True, 0, ""),
    ("tanh",       "tanh",         "tanh({x})",                 "Гиперболические", "same", True, 0, ""),
    ("asinh",      "asinh",        "asinh({x})",                "Гиперболические", "same", True, 0, "Встроенной в HLSL нет, пишется формулой (стоит столько же)"),
    ("acosh",      "acosh",        "acosh({x})",                "Гиперболические", "same", True, 0, "Встроенной в HLSL нет, пишется формулой (стоит столько же)"),
    ("atanh",      "atanh",        "atanh({x})",                "Гиперболические", "same", True, 0, "Встроенной в HLSL нет, пишется формулой (стоит столько же)"),
    # --- derivatives
    ("ddx",        "ddx",          "dFdx({x})",                 "Производные", "same", True, 0, ""),
    ("ddy",        "ddy",          "dFdy({x})",                 "Производные", "same", True, 0, ""),
    ("fwidth",     "fwidth",       "fwidth({x})",               "Производные", "same", True, 0, "abs(ddx)+abs(ddy)"),
    # --- geometric
    ("dot",        "dot",          "{T}(dot({x}, {a}))",        "Векторные", "geo", True, 0, ""),
    ("length",     "length",       "{T}(length({x}))",          "Векторные", "geo", True, 0, ""),
    ("distance",   "distance",     "{T}(distance({x}, {a}))",   "Векторные", "geo", True, 0, ""),
    ("normalize",  "normalize",    "normalize({x})",            "Векторные", "geo", True, 0, ""),
    ("reflect",    "reflect",      "reflect({x}, {a})",         "Векторные", "geo", True, 0, ""),
    ("refract",    "refract",      "refract({x}, {a}, {s})",    "Векторные", "geo", True, 0, ""),
    ("faceforward","faceforward",  "faceforward({a}, {b}, {x})","Векторные", "geo", True, 0, ""),
    ("cross",      "cross",        "cross({x}, {a})",           "Векторные", "vec3", True, 0, "Всегда float3"),
    ("mul_mat",    "mul(matrix, vector)", "uM{D} * {x}",        "Матрицы", "mat", True, 0, "Матрица из uniform"),

    # --- pow with a constant exponent and other exp helpers
    ("pow2",       "pow(x, 2)",    "pow({x}, {T}(2.0))",             "Степени и логарифмы", "same", True, 0, "Константная степень"),
    ("pow3",       "pow(x, 3)",    "pow({x}, {T}(3.0))",             "Степени и логарифмы", "same", True, 0, "Константная степень"),
    ("pow4",       "pow(x, 4)",    "pow({x}, {T}(4.0))",             "Степени и логарифмы", "same", True, 0, "Константная степень"),
    ("pow5",       "pow(x, 5)",    "pow({x}, {T}(5.0))",             "Степени и логарифмы", "same", True, 0, "Константная степень (Schlick Fresnel)"),
    ("pow05",      "pow(x, 0.5)",  "pow({x}, {T}(0.5))",             "Степени и логарифмы", "same", True, 0, "Константная степень"),
    ("pow22",      "pow(x, 2.2)",  "pow({x}, {T}(2.2))",             "Степени и логарифмы", "same", True, 0, "Гамма"),
    ("pow1_22",    "pow(x, 1/2.2)", "pow({x}, {T}(0.4545454545))",   "Степени и логарифмы", "same", True, 0, "Обратная гамма"),
    ("log10",      "log10",        "log2({x}) * 0.30102999566", "Степени и логарифмы", "same", True, 0, "В GLSL нет, разворачивается в log2 × const"),
    ("ldexp",      "ldexp",        "{a} * exp2({x})",           "Степени и логарифмы", "same", True, 0, "HLSL: x * exp2(e)"),
    ("frexp",      "frexp",        "frexp_({x})",               "Степени и логарифмы", "same", True, 1, "Мантисса и порядок"),
    ("modf",       "modf",         "modf_({x})",                "Округление", "same", True, 1, "Дробная и целая часть"),
    # --- comparisons / select
    ("select",     "x > a ? b : x", ("({x} > {a}) ? {b} : {x}", "mix({x}, {b}, greaterThan({x}, {a}))"),
                                                                "Сравнения", "same", True, 0, "Тернарный оператор (поэлементный выбор)"),
    ("cmp",        "(float)(x > a)", ("float({x} > {a})", "{T}(greaterThan({x}, {a}))"),
                                                                "Сравнения", "same", True, 0, "Результат сравнения как 0/1"),
    ("any",        "any",          "{T}(any(greaterThan({x}, {a})))", "Сравнения", "geo", True, 0, "any(x > a)"),
    ("all",        "all",          "{T}(all(greaterThan({x}, {a})))", "Сравнения", "geo", True, 0, "all(x > a)"),
    ("isnan",      "isnan",        ("float(isnan({x}))", "{T}(isnan({x}))"), "Сравнения", "same", True, 0, "Компилятор может считать NaN невозможным"),
    ("isinf",      "isinf",        ("float(isinf({x}))", "{T}(isinf({x}))"), "Сравнения", "same", True, 0, ""),
    ("isfinite",   "isfinite",     ("float(!isinf({x}) && !isnan({x}))", "{T}(not(isinf({x}))) * {T}(not(isnan({x})))"),
                                                                "Сравнения", "same", True, 0, "!isinf && !isnan"),
    # --- branches (real if statements inside a helper; condition on .x for vectors)
    ("if_small",   "if (x > a) x *= b", "if_small_({x}, {a}, {b})", "Ветвления", "same", True, 0,
                   "Короткий if компилятор обычно превращает в select без перехода"),
    ("if_else",    "if (x > a) sin else cos", "if_else_({x}, {a})", "Ветвления", "same", True, 0,
                   "Компилятор считает обе ветки и выбирает результат (переход не делает). sin или cos отдельно = 8"),
    ("if_skip",    "if (x > a) { 8× sin }", "if_skip_({x}, {a})", "Ветвления", "same", True, 0,
                   "Тяжёлая ветка — настоящий переход. Основная цифра — если ветка выполнилась"),
    # --- conversions / half
    ("i2f",        "(float)(int)x", ("float(int({x}))", "{T}({I}({x}))"), "Конвертации", "same", True, 0, "float → int → float"),
    ("f32tof16",   "f32tof16",     {"float": "uintBitsToFloat(packHalf2x16(vec2({x}, 0.0)))", "vec2": "vec2(uintBitsToFloat(packHalf2x16(vec2({x}.x, 0.0))), uintBitsToFloat(packHalf2x16(vec2({x}.y, 0.0))))", "vec4": "vec4(uintBitsToFloat(packHalf2x16(vec2({x}.x, 0.0))), uintBitsToFloat(packHalf2x16(vec2({x}.y, 0.0))), uintBitsToFloat(packHalf2x16(vec2({x}.z, 0.0))), uintBitsToFloat(packHalf2x16(vec2({x}.w, 0.0))))"},
                                                                "Конвертации", "same", True, 0, "float → fp16 (битовый каст бесплатный)"),
    ("f16tof32",   "f16tof32",     {"float": "unpackHalf2x16(floatBitsToUint({x})).x", "vec2": "vec2(unpackHalf2x16(floatBitsToUint({x}.x)).x, unpackHalf2x16(floatBitsToUint({x}.y)).x)", "vec4": "vec4(unpackHalf2x16(floatBitsToUint({x}.x)).x, unpackHalf2x16(floatBitsToUint({x}.y)).x, unpackHalf2x16(floatBitsToUint({x}.z)).x, unpackHalf2x16(floatBitsToUint({x}.w)).x)"},
                                                                "Конвертации", "same", True, 0, "fp16 → float"),
    # --- integers
    ("iadd",       "int a + b",    "{x} + {a}",   "Целые числа", "int",  False, 0, ""),
    ("imul",       "int a * b",    "{x} * {a}",   "Целые числа", "int",  False, 0, ""),
    ("idiv",       "int a / b",    "{a} / {x}",   "Целые числа", "int",  True,  0, "Деление на переменную"),
    ("imod",       "int a % b",    "{a} % {x}",   "Целые числа", "int",  True,  0, ""),
    ("udiv",       "uint a / b",   "{a} / {x}",   "Целые числа", "uint", True,  0, "Беззнаковое деление"),
    ("umod",       "uint a % b",   "{a} % {x}",   "Целые числа", "uint", True,  0, ""),
    ("imin",       "min(int)",     "min({x}, {a})", "Целые числа", "int", True, 0, "max так же"),
    ("iabs",       "abs(int)",     "abs({x})",    "Целые числа", "int",  True,  0, ""),
    ("ishl",       "int a << b",   "{x} << {a}",  "Целые числа", "int",  True,  0, ""),
    ("ushr",       "uint a >> b",  "{x} >> {a}",  "Целые числа", "uint", True,  0, ""),
    ("iand",       "int a & b",    "{x} & {a}",   "Целые числа", "int",  True,  0, "| и ^ стоят так же"),
    ("countbits",  "countbits",    "{T}(bitCount({x}))", "Целые числа", "uint", True, 0, ""),
    ("firstbithigh", "firstbithigh", "{T}(findMSB({x}))", "Целые числа", "uint", True, 0, ""),
    ("firstbitlow", "firstbitlow", "{T}(findLSB({x}))", "Целые числа", "uint", True, 0, ""),
    ("reversebits", "reversebits", "bitfieldReverse({x})", "Целые числа", "uint", True, 0, ""),
    # --- matrices
    ("mul_matmat", "mul(matrix, matrix)", "uM{D} * {x}", "Матрицы", "matmat", True, 0, "Матрица из uniform × матрица"),
    ("transpose",  "transpose",    "transpose({x})", "Матрицы", "matmat", True, 0, ""),
    ("determinant","determinant",  "{x} * determinant({x})", "Матрицы", "matmat", True, 0, "В тесте X * det(X): умножение сливается со сложением"),
    # --- Unity URP / Core RP helpers
    ("unpack_rgb", "UnpackNormalRGBNoScale", "vec4(UnpackNormalRGBNoScale({x}), {x}.w)", "Unity (URP)", "fixed4", True, 0,
                   "UnpackNormal при UNITY_NO_DXT5nm (обычно на мобилках)"),
    ("unpack_ag",  "UnpackNormalAG", "UnpackNormalAG({x}).xyzz", "Unity (URP)", "fixed4", True, 0,
                   "UnpackNormal при UNITY_ASTC_NORMALMAP_ENCODING"),
    ("unpack_rgag","UnpackNormalmapRGorAG", "UnpackNormalmapRGorAG({x}).xyzz", "Unity (URP)", "fixed4", True, 0,
                   "UnpackNormal по умолчанию (DXT5nm/BC5)"),
    ("luminance",  "Luminance",    "vec3(Luminance({x}))", "Unity (URP)", "fixed3", True, 0, ""),
    ("srgb2lin",   "SRGBToLinear", "SRGBToLinear({x})", "Unity (URP)", "fixed3", True, 0, "Точная, через pow"),
    ("srgb2lin_f", "FastSRGBToLinear", "FastSRGBToLinear({x})", "Unity (URP)", "fixed3", True, 0, "Полином; то же, что GammaToLinearSpace"),
    ("lin2srgb",   "LinearToSRGB", "LinearToSRGB({x})", "Unity (URP)", "fixed3", True, 0, "Точная, через pow"),
    ("lin2srgb_f", "FastLinearToSRGB", "FastLinearToSRGB({x})", "Unity (URP)", "fixed3", True, 0, "То же, что LinearToGammaSpace"),
    ("safenorm",   "SafeNormalize", "SafeNormalize({x})", "Unity (URP)", "fixed3", True, 0, "normalize без деления на 0"),
    # --- texture sampling: cost in texture-unit cycles, 1 = one plain tex2D on the same GPU
    ("tex2D",      "tex2D",        "texture(uT2, {x}.xy)",                   "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE2D; эталон = 1"),
    ("tex2Dlod",   "tex2Dlod",     "textureLod(uT2, {x}.xy, {x}.z)",         "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE2D_LOD"),
    ("tex2Dbias",  "tex2Dbias",    "texture(uT2, {x}.xy, {x}.z)",            "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE2D_BIAS"),
    ("tex2Dgrad",  "tex2Dgrad",    "textureGrad(uT2, {x}.xy, {a}.xy, {b}.xy)", "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE2D_GRAD"),
    ("tex2Doffs",  "Sample + offset", "textureOffset(uT2, {x}.xy, ivec2(1, -1))", "Текстуры", "tex", True, 0, "Константное смещение в текселях"),
    ("tex2Dproj",  "tex2Dproj",    "textureProj(uT2, {x}.xyw)",              "Текстуры", "tex", True, 0, "Проективная выборка (деление на w)"),
    ("texload",    "Load",         "texelFetch(uT2, ivec2({x}.xy), 0)",      "Текстуры", "tex", True, 0, "LOAD_TEXTURE2D, без фильтрации"),
    ("texgather",  "Gather",       "textureGather(uT2, {x}.xy)",             "Текстуры", "tex", True, 0, "GATHER_TEXTURE2D, 4 текселя"),
    ("tex3D",      "tex3D",        "texture(uT3, {x}.xyz)",                  "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE3D"),
    ("texCUBE",    "texCUBE",      "texture(uTC, {x}.xyz)",                  "Текстуры", "tex", True, 0, "SAMPLE_TEXTURECUBE"),
    ("texCUBElod", "texCUBElod",   "textureLod(uTC, {x}.xyz, {x}.w)",        "Текстуры", "tex", True, 0, "SAMPLE_TEXTURECUBE_LOD"),
    ("texarray",   "Texture2DArray", "texture(uTA, {x}.xyz)",                "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE2D_ARRAY"),
    ("texshadow",  "SampleCmp (shadow)", "vec4(texture(uTS, {x}.xyz))",     "Текстуры", "tex", True, 0, "SAMPLE_TEXTURE2D_SHADOW, сравнение с глубиной"),
]

# Helper functions placed before main(). {T}/{I} = chain type / its int vector,
# {MINV} = FLT_MIN or HALF_MIN. GLSL ports of Unity Packing.hlsl, Color.hlsl, Common.hlsl.
_POSPOW = "vec3 PositivePow(vec3 b, vec3 p) { return pow(max(abs(b), vec3(5.960464478e-8)), p); }\n"
_UNPACK_AG = ("vec3 UnpackNormalAG(vec4 p) { vec3 n; n.xy = p.ag * 2.0 - 1.0; "
              "n.z = max(1.0e-16, sqrt(1.0 - clamp(dot(n.xy, n.xy), 0.0, 1.0))); return n; }\n")
PRELUDE = {
    "if_small": "{T} if_small_({T} v, {T} a, {T} b) { if ({V0} > {A0}) v = v * b; return v; }\n",
    "if_else": "{T} if_else_({T} v, {T} a) { if ({V0} > {A0}) v = sin(v); else v = cos(v); return v; }\n",
    "if_skip": "{T} if_skip_({T} v, {T} a) { if ({V0} > {A0}) { for (int i = 0; i < 8; i++) v = sin(v); } return v; }\n",
    "frexp": "{T} frexp_({T} v) { {I} e; {T} m = frexp(v, e); return m + {T}(e); }\n",
    "modf": "{T} modf_({T} v) { {T} i; {T} f = modf(v, i); return f * i; }\n",
    "unpack_rgb": "vec3 UnpackNormalRGBNoScale(vec4 p) { return p.rgb * 2.0 - 1.0; }\n",
    "unpack_ag": _UNPACK_AG,
    "unpack_rgag": _UNPACK_AG + "vec3 UnpackNormalmapRGorAG(vec4 p) { p.a *= p.r; return UnpackNormalAG(p); }\n",
    "luminance": "float Luminance(vec3 c) { return dot(c, vec3(0.2126729, 0.7151522, 0.0721750)); }\n",
    "srgb2lin": _POSPOW + ("vec3 SRGBToLinear(vec3 c) { vec3 lo = c / 12.92; "
                           "vec3 hi = PositivePow((c + 0.055) / 1.055, vec3(2.4)); "
                           "return mix(hi, lo, lessThanEqual(c, vec3(0.04045))); }\n"),
    "srgb2lin_f": "vec3 FastSRGBToLinear(vec3 c) { return c * (c * (c * 0.305306011 + 0.682171111) + 0.012522878); }\n",
    "lin2srgb": _POSPOW + ("vec3 LinearToSRGB(vec3 c) { vec3 lo = c * 12.92; "
                           "vec3 hi = PositivePow(c, vec3(1.0 / 2.4)) * 1.055 - 0.055; "
                           "return mix(hi, lo, lessThanEqual(c, vec3(0.0031308))); }\n"),
    "lin2srgb_f": _POSPOW + "vec3 FastLinearToSRGB(vec3 c) { return clamp(1.055 * PositivePow(c, vec3(0.416666667)) - 0.055, 0.0, 1.0); }\n",
    "safenorm": "vec3 SafeNormalize(vec3 v) { return v * inversesqrt(max({MINV}, dot(v, v))); }\n",
}
MINV = {"highp": "1.175494351e-38", "mediump": "6.103515625e-5"}


def glsl_type(kind, dim):
    """GLSL type measured for a function kind in a variant of size dim (1, 2, 4)."""
    if kind == "same":
        return {1: "float", 2: "vec2", 4: "vec4"}[dim]
    if kind in ("geo", "mat"):
        return {1: "vec3", 2: "vec2", 4: "vec4"}[dim]
    if kind == "matmat":
        return {1: "mat3", 2: "mat2", 4: "mat4"}[dim]
    if kind == "int":
        return {1: "int", 2: "ivec2", 4: "ivec4"}[dim]
    if kind == "uint":
        return {1: "uint", 2: "uvec2", 4: "uvec4"}[dim]
    return {"vec3": "vec3", "fixed3": "vec3", "fixed4": "vec4", "tex": "vec4"}[kind]


def _size(t):
    return int(t[-1]) if t[-1].isdigit() else 1


def _ivec(t):
    n = _size(t)
    return "int" if n == 1 else f"ivec{n}"


def glsl_expr(expr, t):
    """Pick the form of expr for GLSL type t and fill size placeholders."""
    if isinstance(expr, dict):
        expr = expr[t]
    elif isinstance(expr, tuple):
        expr = expr[0] if t == "float" else expr[1]
    return expr.replace("{D}", str(_size(t))).replace("{I}", _ivec(t))


def prelude(fid, t, prec):
    p = PRELUDE.get(fid, "")
    v0, a0 = ("v", "a") if t == "float" else ("v.x", "a.x")
    return (p.replace("{T}", t).replace("{I}", _ivec(t)).replace("{MINV}", MINV[prec])
             .replace("{V0}", v0).replace("{A0}", a0))


def hlsl_type(prec, t):
    half = prec == "mediump"
    n = str(_size(t)) if t[-1].isdigit() else ""
    if t.startswith("mat"):
        return ("half" if half else "float") + f"{n}x{n}"
    if t.startswith(("int", "ivec")):
        return ("min16int" if half else "int") + n
    if t.startswith(("uint", "uvec")):
        return ("min16uint" if half else "uint") + n
    return ("half" if half else "float") + n


# HLSL (Unity) form of each measured expression, same argument order as the GLSL.
# None = no HLSL intrinsic (GLSL-only builtin).
HLSL_EXPR = {
    "add": "x + a", "mul": "x * a", "mad": "mad(x, a, b)", "div": "a / x", "rcp": "rcp(x)",
    "abs": "abs(x)", "sign": "sign(x)", "min": "min(x, a)", "max": "max(x, a)",
    "clamp": "clamp(x, a, b)", "saturate": "saturate(x)", "lerp": "lerp(x, a, b)",
    "step": "step(a, x)", "smoothstep": "smoothstep(a, x, b)",
    "floor": "floor(x)", "ceil": "ceil(x)", "round": "round(x)", "trunc": "trunc(x)",
    "frac": "frac(x)", "fmod": "fmod(a, x)", "mod": "a - x * floor(a / x)",
    "sqrt": "sqrt(x)", "rsqrt": "rsqrt(x)", "exp2": "exp2(x)", "exp": "exp(x)",
    "log2": "log2(x)", "log": "log(x)", "pow": "pow(x, a)",
    "sin": "sin(x)", "cos": "cos(x)", "sincos": "sincos(x, s, c); s + c", "tan": "tan(x)",
    "asin": "asin(x)", "acos": "acos(x)", "atan": "atan(x)", "atan2": "atan2(x, a)",
    "radians": "radians(x)", "degrees": "degrees(x)",
    "sinh": "sinh(x)", "cosh": "cosh(x)", "tanh": "tanh(x)",
    "asinh": "log(x + sqrt(x * x + 1))", "acosh": "log(x + sqrt(x * x - 1))", "atanh": "0.5 * log((1 + x) / (1 - x))",
    "ddx": "ddx(x)", "ddy": "ddy(x)", "fwidth": "fwidth(x)",
    "dot": "dot(x, a)", "length": "length(x)", "distance": "distance(x, a)",
    "normalize": "normalize(x)", "reflect": "reflect(x, a)", "refract": "refract(x, a, s)",
    "faceforward": "faceforward(a, b, x)", "cross": "cross(x, a)", "mul_mat": "mul(M, x)",
    "pow2": "pow(x, 2)", "pow3": "pow(x, 3)", "pow4": "pow(x, 4)", "pow5": "pow(x, 5)",
    "pow05": "pow(x, 0.5)", "pow22": "pow(x, 2.2)", "pow1_22": "pow(x, 1.0 / 2.2)",
    "log10": "log10(x)", "ldexp": "ldexp(a, x)", "frexp": "frexp(x, e)", "modf": "modf(x, ip)",
    "if_small": "if (x > a) x *= b;", "if_else": "if (x > a) x = sin(x); else x = cos(x);",
    "if_skip": "if (x > a) { for (int i = 0; i < 8; i++) x = sin(x); }",
    "select": "x > a ? b : x", "cmp": "(float)(x > a)", "any": "any(x > a)", "all": "all(x > a)",
    "isnan": "isnan(x)", "isinf": "isinf(x)", "isfinite": "isfinite(x)",
    "i2f": "(float)(int)x", "f32tof16": "asfloat(f32tof16(x))", "f16tof32": "f16tof32(asuint(x))",
    "iadd": "x + a", "imul": "x * a", "idiv": "a / x", "imod": "a % x",
    "udiv": "a / x", "umod": "a % x", "imin": "min(x, a)", "iabs": "abs(x)",
    "ishl": "x << a", "ushr": "x >> a", "iand": "x & a",
    "countbits": "countbits(x)", "firstbithigh": "firstbithigh(x)",
    "firstbitlow": "firstbitlow(x)", "reversebits": "reversebits(x)",
    "mul_matmat": "mul(M, X)", "transpose": "transpose(X)", "determinant": "determinant(X)",
    "unpack_rgb": "UnpackNormalRGBNoScale(x)", "unpack_ag": "UnpackNormalAG(x)",
    "unpack_rgag": "UnpackNormalmapRGorAG(x)", "luminance": "Luminance(x)",
    "srgb2lin": "SRGBToLinear(x)", "srgb2lin_f": "FastSRGBToLinear(x)",
    "lin2srgb": "LinearToSRGB(x)", "lin2srgb_f": "FastLinearToSRGB(x)", "safenorm": "SafeNormalize(x)",
    "tex2D": "tex.Sample(s, uv)", "tex2Dlod": "tex.SampleLevel(s, uv, lod)", "tex2Dbias": "tex.SampleBias(s, uv, bias)",
    "tex2Dgrad": "tex.SampleGrad(s, uv, ddx, ddy)", "tex2Doffs": "tex.Sample(s, uv, int2(1, -1))",
    "tex2Dproj": "tex2Dproj(s, uvw)", "texload": "tex.Load(int3(xy, 0))", "texgather": "tex.Gather(s, uv)",
    "tex3D": "tex3D.Sample(s, uvw)", "texCUBE": "cube.Sample(s, dir)", "texCUBElod": "cube.SampleLevel(s, dir, lod)",
    "texarray": "arr.Sample(s, float3(uv, slice))", "texshadow": "shadow.SampleCmp(s, uv, depth)",
}
