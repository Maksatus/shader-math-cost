"""Vulkan variants from Unity's "Compile and show code" text (ShaderUtil.OpenCompiledShader, the build's compiler):
its glslang disassembly is assembled back into SPIR-V with spirv-as; identical to the build's code but the generator
word of the header. CompileVariant for Vulkan fails in Unity 6.4 on shaders with globals outside cbuffers (Forward+,
SSAO, URP post, CoreBlit): "Compiler reported top-level params outside of cbuffers", with keywords the pass declares
and either forExternalTool, while the variants it does compile equal these byte for byte."""
import os
import re
import struct
import subprocess

# operand positions (after the result type and id) that are literals: {i}, or "rest<i>" for i and after
LITERAL = {
    "Constant": "rest0", "SpecConstant": "rest0", "TypeInt": "rest0", "TypeFloat": "rest0", "TypeVector": {1},
    "TypeMatrix": {1}, "TypeImage": "rest2", "Decorate": "rest1", "MemberDecorate": "rest1", "MemberName": {1},
    "CompositeExtract": "rest1", "CompositeInsert": "rest2", "VectorShuffle": "rest2", "ExecutionMode": "rest1",
    "Source": "rest0", "Line": "rest1", "SourceExtension": "rest0", "Switch": "switch", "LoopMerge": "rest2",
    "SelectionMerge": "rest1", "Capability": "rest0", "MemoryModel": "rest0", "TypePointer": {0}, "Variable": {0},
    "EntryPoint": {0}, "Function": {0}, "Name": "rest1", "ExtInstImport": "rest0", "Extension": "rest0",
    "Store": "rest2", "Load": "rest1", "CopyMemory": "rest2", "ModuleProcessed": "rest0", "String": "rest0",
    "DecorateString": "rest1", "MemberDecorateString": "rest1", "ExecutionModeId": {1},
}
SPEC_OPS = {126: "SNegate", 127: "FNegate", 128: "IAdd", 129: "FAdd", 130: "ISub", 131: "FSub", 132: "IMul",
            133: "FMul", 134: "UDiv", 135: "SDiv", 136: "FDiv", 137: "UMod", 138: "SRem", 139: "SMod",
            164: "LogicalEqual", 165: "LogicalNotEqual", 166: "LogicalOr", 167: "LogicalAnd", 168: "LogicalNot",
            169: "Select", 170: "IEqual", 171: "INotEqual", 172: "UGreaterThan", 173: "SGreaterThan",
            174: "UGreaterThanEqual", 175: "SGreaterThanEqual", 176: "ULessThan", 177: "SLessThan",
            178: "ULessThanEqual", 179: "SLessThanEqual", 194: "ShiftRightLogical", 195: "ShiftRightArithmetic",
            196: "ShiftLeftLogical", 197: "BitwiseOr", 198: "BitwiseXor", 199: "BitwiseAnd", 200: "Not",
            79: "VectorShuffle", 81: "CompositeExtract", 82: "CompositeInsert", 113: "UConvert", 114: "SConvert",
            115: "FConvert", 116: "QuantizeToF16", 124: "Bitcast"}
STAGES = {"Vertex": "vert", "Fragment": "frag"}
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\S+')
RESULT = re.compile(r"^(\d+)(?:\([^)]*\))?:\s+(.*)$")


class CompiledTextError(RuntimeError):
    pass


def blocks(text):
    """-> {(subshader, pass index, keywords frozenset, stage): disassembly}."""
    out = {}
    sub = pas = -1
    kw = frozenset()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("SubShader {"):
            sub, pas = sub + 1, -1
        elif ln.startswith(" Pass {"):
            pas += 1
        elif ln.startswith("Keywords:"):
            words = ln[len("Keywords:"):].split()
            kw = frozenset(w for w in words if w != "<none>")
        m = re.match(r"^Disassembly for (\w+):", ln)
        if m:
            body, i = [], i + 1
            while i < len(lines):
                s = lines[i]
                if s.strip() and not s[0].isspace() and not s.startswith("//") and not RESULT.match(s):
                    break
                body.append(s)
                i += 1
            if m.group(1) in STAGES:
                out[(sub, pas, kw, STAGES[m.group(1)])] = "\n".join(body)
            continue
        i += 1
    return out


def _literal_at(op, i):
    rule = LITERAL.get(op)
    if rule is None:
        return False
    if isinstance(rule, set):
        return i in rule
    if rule == "switch":
        return i >= 2 and i % 2 == 0
    return i >= int(rule[4:])


def _join_cases(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:case\s+(-?\d+)|default):\s*(\d+)", line)
        if m and out:
            if m.group(1) is not None:
                out[-1] += f" {m.group(1)} {m.group(2)}"
        else:
            out.append(line)
    return out


def _image(args):
    """glslang prints <type> <Dim> [depth] [array] [multi-sampled] [sampled|nonsampled] format:<F> [access]."""
    words = args[2:]
    sampled = 1 if "sampled" in words else 2 if "nonsampled" in words else 0
    fmt = next((w.split(":", 1)[1] for w in words if w.startswith("format:")), "Unknown")
    return [args[0], args[1], str(int("depth" in words)), str(int("array" in words)),
            str(int("multi-sampled" in words)), str(sampled), fmt] + \
        [w for w in words if w in ("ReadOnly", "WriteOnly", "ReadWrite")]


def _constant(kind, v):
    """glslang prints constants as their bit patterns."""
    v = int(v)
    if kind == ("float", 32):
        return float(struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]).hex()
    if kind == ("float", 16):
        return float(struct.unpack("<e", struct.pack("<H", v & 0xFFFF))[0]).hex()
    if kind and kind[0] == "sint" and v >= 1 << (kind[1] - 1):
        return str(v - (1 << kind[1]))
    return str(v)


def to_spvasm(disassembly):
    out, types = [], {}
    for line in _join_cases(disassembly):
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        m = RESULT.match(s)
        res, rest = (m.group(1), m.group(2)) if m else (None, s)
        toks = TOKEN.findall(rest)
        typ = None
        if res and re.match(r"^\d+(\([^)]*\))?$", toks[0]):
            typ, toks = toks[0].split("(")[0], toks[1:]
        op, args = toks[0], toks[1:]
        if op == "TypeFloat":
            types[res] = ("float", int(args[0]))
        elif op == "TypeInt":
            types[res] = ("sint" if args[1] == "1" else "uint", int(args[0]))
        elif op == "TypeImage":
            args = _image(args)
        elif op == "SpecConstantOp":
            n = re.match(r"^(\d+)", args[0])
            if n and int(n.group(1)) not in SPEC_OPS:
                raise CompiledTextError(f"OpSpecConstantOp with opcode {n.group(1)}")
            args = [SPEC_OPS[int(n.group(1))] if n else args[0]] + args[1:]
        head = (f"%{res} = " if res else "") + "Op" + op + (f" %{typ}" if typ else "")
        if op in ("Constant", "SpecConstant") and typ in types:
            out.append(f"{head} " + " ".join(_constant(types[typ], a) for a in args))
            continue
        conv = []
        for i, t in enumerate(args):
            n = re.match(r"^(-?\d+)(?:\(([^)]*)\))?$", t)
            if t.startswith('"') or not n:
                conv.append(t)
            elif op == "ExtInst" and i == 1:
                conv.append(n.group(2) or n.group(1))
            else:
                conv.append(n.group(1) if _literal_at(op, i) else "%" + n.group(1))
        out.append(head + "".join(" " + c for c in conv))
    return "\n".join(out) + "\n"


def assemble(disassembly, out_path, spirv_as, version="1.0"):
    """Writes SPIR-V to out_path; raises CompiledTextError."""
    src = out_path + ".spvasm"
    with open(src, "w", encoding="utf-8") as f:
        f.write(to_spvasm(disassembly))
    try:
        r = subprocess.run([spirv_as, "--preserve-numeric-ids", "--target-env", f"spv{version}", "-o", out_path, src],
                           capture_output=True, text=True, timeout=120)
    finally:
        os.remove(src)
    if r.returncode:
        raise CompiledTextError(f"spirv-as: {(r.stderr or r.stdout).strip()[:300]}")
    m = re.search(r"Generated by \(magic number\): ([0-9a-fA-F]+)", disassembly)
    if m:  # the build's generator word, so the file equals the build's byte for byte
        with open(out_path, "r+b") as f:
            f.seek(8)
            f.write(struct.pack("<I", int(m.group(1), 16)))


def version_of(disassembly):
    """'// Module Version 10300' -> '1.3'."""
    m = re.search(r"Module Version (\d+)", disassembly)
    v = int(m.group(1), 16) if m else 0x10000
    return f"{v >> 16}.{(v >> 8) & 0xFF}"
