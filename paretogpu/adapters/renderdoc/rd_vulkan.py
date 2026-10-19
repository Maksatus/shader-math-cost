"""Pipeline state of every draw and dispatch of a Vulkan capture from its structured data, with no replay moves (a move
costs ~0.2 s on a device). Imported inside qrenderdoc's python (stdlib, rd_spirv). Buffer contents = the memory's
Initial Contents + the host writes (coherent map writes, flushes), vkCmdUpdateBuffer / vkCmdCopyBuffer and descriptor
updates as events replay them; shader writes to uniform or index memory are not followed (none in Unity frames).
Indirect draws take their state here and their counts from the replay's action (no unique indices)."""
import base64
import hashlib
from array import array

import rd_spirv

COMPARE = ("Never", "Less", "Equal", "LessEqual", "Greater", "NotEqual", "GreaterEqual", "AlwaysTrue")
CULL = ("NoCull", "Front", "Back", "FrontAndBack")
BLEND = ("Zero", "One", "SrcColor", "OneMinusSrcColor", "DstColor", "OneMinusDstColor", "SrcAlpha",
         "OneMinusSrcAlpha", "DstAlpha", "OneMinusDstAlpha")
DYNAMIC = (8, 9)  # VK_DESCRIPTOR_TYPE_*_BUFFER_DYNAMIC
UNUSED = 0xFFFFFFFF
STAGES = {1: "vert", 16: "frag", 32: "comp"}
DYN_VIEWPORT, DYN_SCISSOR, DYN_DEPTH_BIAS = 0, 1, 3
SETUP = {"vkCreateBuffer", "vkAllocateMemory", "vkBindBufferMemory", "vkCreateImageView", "vkCreateShaderModule",
         "vkCreateDescriptorSetLayout", "vkAllocateDescriptorSets", "vkCreateRenderPass", "vkCreateRenderPass2",
         "vkCreateFramebuffer", "vkCreateGraphicsPipelines", "vkCreateComputePipelines", "Internal::Initial Contents"}
DRAWS = {"vkCmdDraw", "vkCmdDrawIndexed", "vkCmdDrawIndirect", "vkCmdDrawIndexedIndirect", "vkCmdDrawIndirectCount",
         "vkCmdDrawIndexedIndirectCount", "vkCmdDrawIndirectCountKHR", "vkCmdDrawIndexedIndirectCountKHR"}
DISPATCHES = {"vkCmdDispatch", "vkCmdDispatchIndirect"}
EVENTS = {"vkUpdateDescriptorSetWithTemplate", "vkUpdateDescriptorSets", "Internal::Coherent Mapped Memory Write",
          "vkFlushMappedMemoryRanges", "vkBeginCommandBuffer", "vkCmdBeginRenderPass", "vkCmdBeginRenderPass2",
          "vkCmdNextSubpass", "vkCmdNextSubpass2", "vkCmdEndRenderPass", "vkCmdEndRenderPass2", "vkCmdBindPipeline",
          "vkCmdBindDescriptorSets", "vkCmdBindVertexBuffers", "vkCmdBindIndexBuffer", "vkCmdSetViewport",
          "vkCmdSetScissor", "vkCmdSetDepthBias", "vkCmdUpdateBuffer", "vkCmdCopyBuffer", "vkCmdBeginRendering",
          "vkCmdBeginRenderingKHR", "vkCmdEndRendering", "vkCmdEndRenderingKHR"} | DRAWS | DISPATCHES
INDEX = {0: ("H", 0xFFFF), 1: ("I", 0xFFFFFFFF), 1000265000: ("B", 0xFF)}


def value(o, buffers):
    """SDObject -> plain python; byte buffers -> bytes."""
    t = str(o.type.basetype).split(".")[-1]
    n = o.NumChildren()
    if t in ("Chunk", "Struct"):
        return {o.GetChild(i).name: value(o.GetChild(i), buffers) for i in range(n)}
    if t == "Array":
        return [value(o.GetChild(i), buffers) for i in range(n)]
    if t == "Buffer":
        return bytes(buffers[o.AsInt()])
    if t == "Null":
        return None
    if t == "String":
        return o.AsString()
    if t == "Float":
        return o.AsFloat()
    return o.AsInt()


class Frame:
    """Feed chunks in event order (setup chunks first); draw states collected in self.calls {event: state}."""

    def __init__(self):
        self.buffers, self.memory, self.views, self.modules, self.layouts = {}, {}, {}, {}, {}
        self.set_layout, self.sets, self.passes, self.framebuffers, self.pipelines = {}, {}, {}, {}, {}
        self.calls, self.render, self.target, self.reflected = {}, None, None, {}
        self.reset()

    def reset(self):
        self.bound = {0: None, 1: None}
        self.bound_sets = {0: {}, 1: {}}
        self.vbs, self.ib, self.viewport, self.scissor, self.bias = {}, None, None, None, None

    def setup(self, name, c):
        ci = c.get("CreateInfo")
        if name == "vkCreateBuffer":
            self.buffers[c["Buffer"]] = {"size": ci["size"], "memory": None, "offset": 0}
        elif name == "vkAllocateMemory":
            self.memory.setdefault(c["Memory"], bytearray(c["AllocateInfo"]["allocationSize"]))
        elif name == "vkBindBufferMemory":
            self.buffers[c["buffer"]].update(memory=c["memory"], offset=c["memoryOffset"])
        elif name == "vkCreateImageView":
            self.views[c["View"]] = ci["image"]
        elif name == "vkCreateShaderModule":
            self.modules[c["ShaderModule"]] = ci["pCode"]
        elif name == "vkCreateDescriptorSetLayout":
            self.layouts[c["SetLayout"]] = sorted((b["binding"], b["descriptorType"], b["descriptorCount"])
                                                  for b in ci["pBindings"] or [])
        elif name == "vkAllocateDescriptorSets":
            self.set_layout[c["DescriptorSet"]] = c["AllocateInfo"]["pSetLayouts"][0]
        elif name in ("vkCreateRenderPass", "vkCreateRenderPass2"):
            self.passes[c["RenderPass"]] = [
                ([r["attachment"] for r in s["pColorAttachments"] or []],
                 [r["attachment"] for r in s["pResolveAttachments"] or []],
                 (s["pDepthStencilAttachment"] or {}).get("attachment", UNUSED)) for s in ci["pSubpasses"]]
        elif name == "vkCreateFramebuffer":
            self.framebuffers[c["Framebuffer"]] = [self.views.get(v) for v in ci["pAttachments"] or []]
        elif name in ("vkCreateGraphicsPipelines", "vkCreateComputePipelines"):
            self.pipelines[c["Pipeline"]] = ci
        elif name == "Internal::Initial Contents":
            if c["type"] == 5:  # memory
                self.memory[c["id"]][:len(c["Contents"])] = c["Contents"]
            elif c["type"] == 19:  # descriptor set
                self.sets[c["id"]] = [dict(s) for s in c["Bindings"]]

    def _slot(self, dset, binding, elem):
        k = 0
        for b, _, n in self.layouts[self.set_layout[dset]]:
            if b == binding:
                return k + elem
            k += n
        raise ValueError(f"descriptor set {dset} has no binding {binding}")

    def _write(self, w):
        dset, binding, elem = w["dstSet"], w["dstBinding"], w["dstArrayElement"]
        infos = w.get("pBufferInfo") or w.get("pImageInfo") or w.get("pTexelBufferView") or []
        layout = self.layouts[self.set_layout[dset]]
        for info in infos[:w["descriptorCount"]]:
            n = next(n for b, _, n in layout if b == binding)
            if elem >= n:
                binding, elem = next(b for b, _, _ in layout if b > binding), 0
            slot = self.sets.setdefault(dset, [])
            k = self._slot(dset, binding, elem)
            while len(slot) <= k:
                slot.append({})
            if isinstance(info, dict) and "buffer" in info:
                slot[k] = {"type": w["descriptorType"], "resource": info["buffer"], "offset": info["offset"],
                           "range": info["range"]}
            else:
                slot[k] = {"type": w["descriptorType"], "resource": None}
            elem += 1

    def event(self, eid, name, c):
        if name in ("vkUpdateDescriptorSetWithTemplate", "vkUpdateDescriptorSets"):
            for w in c.get("Decoded Writes") or c.get("pDescriptorWrites") or []:
                self._write(w)
            for cp in c.get("pDescriptorCopies") or []:
                for i in range(cp["descriptorCount"]):
                    src = self.sets[cp["srcSet"]][self._slot(cp["srcSet"], cp["srcBinding"], cp["srcArrayElement"] + i)]
                    self.sets[cp["dstSet"]][self._slot(cp["dstSet"], cp["dstBinding"], cp["dstArrayElement"] + i)] = \
                        dict(src)
        elif name in ("Internal::Coherent Mapped Memory Write", "vkFlushMappedMemoryRanges"):
            r, data = c["MemRange"], c["MapData"]
            self.memory[r["memory"]][r["offset"]:r["offset"] + len(data)] = data
        elif name == "vkBeginCommandBuffer":
            self.reset()
        elif name in ("vkCmdBeginRenderPass", "vkCmdBeginRenderPass2"):
            b = c["RenderPassBegin"]
            self.render = [b["renderPass"], b["framebuffer"], 0]
            self._subpass()
        elif name in ("vkCmdNextSubpass", "vkCmdNextSubpass2"):
            self.render[2] += 1
            self._subpass()
        elif name in ("vkCmdBeginRendering", "vkCmdBeginRenderingKHR"):
            ri = c["pRenderingInfo"]
            views = [a["imageView"] for a in ri["pColorAttachments"] or []] +                     [a["resolveImageView"] for a in ri["pColorAttachments"] or []]
            depth = (ri.get("pDepthAttachment") or {}).get("imageView")
            self.target = ([self.views[v] for v in views if v], self.views.get(depth) if depth else None)
        elif name in ("vkCmdEndRenderPass", "vkCmdEndRenderPass2", "vkCmdEndRendering", "vkCmdEndRenderingKHR"):
            self.render = self.target = None
        elif name == "vkCmdUpdateBuffer":
            self.write(c["destBuffer"], c["destOffset"], c["pData"])
        elif name == "vkCmdCopyBuffer" and self.buffers[c["srcBuffer"]]["memory"] in self.memory:
            for r in c["pRegions"]:
                self.write(c["destBuffer"], r["dstOffset"], self.read(c["srcBuffer"], r["srcOffset"], r["size"]))
        elif name == "vkCmdBindPipeline":
            self.bound[c["pipelineBindPoint"]] = c["pipeline"]
        elif name == "vkCmdBindDescriptorSets":
            offsets = list(c["pDynamicOffsets"] or [])
            for i, dset in enumerate(c["pDescriptorSets"]):
                dyn = {}
                for b, t, n in self.layouts[self.set_layout[dset]]:
                    if t in DYNAMIC:
                        for e in range(n):
                            dyn[(b, e)] = offsets.pop(0)
                self.bound_sets[c["pipelineBindPoint"]][c["firstSet"] + i] = (dset, dyn)
        elif name == "vkCmdBindVertexBuffers":
            for i, (buf, off) in enumerate(zip(c["pBuffers"], c["pOffsets"])):
                self.vbs[c["firstBinding"] + i] = (buf, off)
        elif name == "vkCmdBindIndexBuffer":
            self.ib = (c["buffer"], c["offset"], c["indexType"])
        elif name == "vkCmdSetViewport" and c["firstViewport"] == 0:
            self.viewport = c["pViewports"][0]
        elif name == "vkCmdSetScissor" and c["firstScissor"] == 0:
            self.scissor = c["pScissors"][0]
        elif name == "vkCmdSetDepthBias":
            self.bias = [c["depthBias"], c["depthBiasClamp"], c["slopeScaledDepthBias"]]
        elif name in DRAWS:
            self.calls[eid] = self._draw(name, c)
        elif name in DISPATCHES:
            self.calls[eid] = self._dispatch(c)

    def _subpass(self):
        rp, fb, sub = self.render
        colors, resolves, depth_ref = self.passes[rp][sub]
        images = self.framebuffers[fb]
        self.target = ([images[a] for a in colors + resolves if a != UNUSED],  # as RenderDoc's output targets
                       images[depth_ref] if depth_ref != UNUSED else None)

    def write(self, buf, offset, data):
        b = self.buffers[buf]
        if b["memory"] in self.memory:
            at = b["offset"] + offset
            self.memory[b["memory"]][at:at + len(data)] = data

    def read(self, buf, offset, size):
        """-> bytes of a buffer at the current point, cut at its end (as GetBufferData)."""
        b = self.buffers[buf]
        size = max(0, min(size, b["size"] - offset))
        if b["memory"] not in self.memory:
            raise ValueError(f"buffer {buf}: its memory {b['memory']} has no contents in the capture")
        at = b["offset"] + offset
        return bytes(self.memory[b["memory"]][at:at + size])

    def interface(self, module, entry):
        if (module, entry) not in self.reflected:
            self.reflected[(module, entry)] = rd_spirv.interface(self.modules[module], entry)
        return self.reflected[(module, entry)]

    def _blocks(self, bp, stages):
        out = {}
        for module, entry in stages.values():
            for s, b, size in self.interface(module, entry)["blocks"]:
                dset, dyn = self.bound_sets[bp].get(s, (None, {}))
                slot = self.sets.get(dset, [])
                k = self._slot(dset, b, 0) if dset is not None else None
                d = slot[k] if k is not None and k < len(slot) else {}
                if d.get("resource") in (None, 0):
                    continue
                off = d["offset"] + dyn.get((b, 0), 0)
                rng = self.buffers[d["resource"]]["size"] - d["offset"] if d["range"] in (-1, (1 << 64) - 1) else d["range"]
                out[f"@{s}.{b}"] = base64.b64encode(self.read(d["resource"], off, size or min(rng, 65536))).decode()
        return out

    def _stages(self, pipe):
        ci = self.pipelines[pipe]
        stages = ci.get("pStages") or [ci["stage"]]
        return {STAGES[s["stage"]]: (s["module"], s["pName"]) for s in stages if s["stage"] in STAGES}

    def _draw(self, name, c):
        pipe = self.bound[0]
        ci = self.pipelines[pipe]
        stages = self._stages(pipe)
        outs, depth = self.target
        cb = ci.get("pColorBlendState") or {}
        att = (cb.get("pAttachments") or [None])[0]
        ds = ci.get("pDepthStencilState") or {}
        rs = ci["pRasterizationState"]
        dyn = set((ci.get("pDynamicState") or {}).get("pDynamicStates") or [])
        vp = self.viewport if DYN_VIEWPORT in dyn else ci["pViewportState"]["pViewports"][0]
        sc = self.scissor if DYN_SCISSOR in dyn else ci["pViewportState"]["pScissors"][0]
        bias = None
        if rs["depthBiasEnable"]:
            bias = self.bias if DYN_DEPTH_BIAS in dyn else [rs["depthBiasConstantFactor"], rs["depthBiasClamp"],
                                                            rs["depthBiasSlopeFactor"]]
        bindings = sorted(self.vbs)
        indexed = "Indexed" in name
        state = {
            "pipeline": pipe, "stages": stages, "blocks": self._blocks(0, stages), "outs": outs, "depth": depth,
            "blend": (f"{BLEND[att['srcColorBlendFactor']] if att['srcColorBlendFactor'] < len(BLEND) else 'One'} "
                      f"{BLEND[att['dstColorBlendFactor']] if att['dstColorBlendFactor'] < len(BLEND) else 'Zero'}"
                      if att and att["blendEnable"] else "One Zero"),
            "write_mask": att["colorWriteMask"] if att else 0,
            "ztest": COMPARE[ds["depthCompareOp"]] if ds.get("depthTestEnable") else "Disabled",
            "zwrite": bool(ds.get("depthTestEnable") and ds.get("depthWriteEnable")),
            "cull": CULL[rs["cullMode"]], "vbs": [self.vbs[b][0] for b in bindings], "indexed": indexed,
            "raster": {"viewport": [vp["x"], vp["y"], vp["width"], vp["height"]],
                       "scissor": [sc["offset"]["x"], sc["offset"]["y"], sc["extent"]["width"], sc["extent"]["height"]],
                       "depth_clip": not rs["depthClampEnable"], "bias": bias,
                       "samples": ci["pMultisampleState"]["rasterizationSamples"], "cull": CULL[rs["cullMode"]],
                       "front_ccw": rs["frontFace"] == 0, "depth": depth or 0,
                       "ztest": bool(ds.get("depthTestEnable")), "zfunc": COMPARE[ds.get("depthCompareOp", 7)],
                       "zwrite": bool(ds.get("depthWriteEnable")),
                       "topology": ci["pInputAssemblyState"]["topology"]},
            "unique_vertices": None}
        if indexed and c.get("indexCount") and self.ib and self.ib[2] in INDEX:
            code, restart = INDEX[self.ib[2]]
            idx = array(code)
            raw = self.read(self.ib[0], self.ib[1] + c["firstIndex"] * idx.itemsize, c["indexCount"] * idx.itemsize)
            idx.frombytes(raw[:len(raw) - len(raw) % idx.itemsize])
            seen = set(idx)
            seen.discard(restart)
            state["unique_vertices"] = len(seen)
        return state

    def _dispatch(self, c):
        pipe = self.bound[1]
        stages = self._stages(pipe)
        module, entry = stages["comp"]
        return {"pipeline": pipe, "stages": stages, "blocks": self._blocks(1, stages),
                "groups": [c["x"], c["y"], c["z"]] if "x" in c else [None, None, None],
                "group_size": self.interface(module, entry)["local_size"]}


def frame(sd, events):
    """sd: the capture's structured data; events: [(event id, chunk index)] of the replay (its event order runs the
    secondary command buffers inline) -> Frame with .calls {event: state}."""
    f = Frame()
    replayed = {ci for _, ci in events}
    for ci, ch in enumerate(sd.chunks):
        if ci in replayed or ch.name not in SETUP:
            continue
        if ch.name == "Internal::Initial Contents" and ch.GetChild(0).AsInt() not in (5, 19):
            continue
        f.setup(ch.name, value(ch, sd.buffers))
    for eid, ci in sorted(events):
        ch = sd.chunks[ci]
        if ch.name in EVENTS:
            f.event(eid, ch.name, value(ch, sd.buffers))
    return f


def shader_file(raw, entry, short):
    """-> (hash, file name) of a stage's SPIR-V, as rd_device writes it."""
    h = hashlib.sha1(raw + entry.encode()).hexdigest()[:16]
    return h, f"{h}.{short}.spv"


def describe(state, names, textures, write):
    """A call's state -> rd_device's draw record (without "event" and the counters). names {id: name or None},
    textures {id: {"width", "height", "samples", "swapchain"}}, write(raw, entry, short) -> {"hash", "file", "entry",
    "bytes"} (the SPIR-V file)."""
    sh = {}
    for short, (module, entry) in state["stages"].items():
        sh[short] = {**write(module, entry, short), "name": names.get(module)}
    if "groups" in state:
        return {"shaders": sh, "blocks": state["blocks"], "groups": state["groups"], "group_size": state["group_size"]}
    target = (state["outs"] or [state["depth"]])[0]
    tex = textures.get(target)
    return {
        "shaders": sh, "blocks": state["blocks"], "pipeline": names.get(state["pipeline"]),
        "rt": {"id": target, "name": names.get(target) or "", "width": tex["width"] if tex else None,
               "height": tex["height"] if tex else None, "samples": tex["samples"] if tex else 1,
               "count": len(state["outs"]), "backbuffer": bool(tex and tex["swapchain"])},
        "blend": state["blend"], "write_mask": state["write_mask"], "ztest": state["ztest"], "zwrite": state["zwrite"],
        "cull": state["cull"], "meshes": list(dict.fromkeys(m for m in (names.get(b) or "" for b in state["vbs"]) if m)),
        "unique_vertices": state["unique_vertices"], "indexed": state["indexed"]}


def shaders_of(f, stage):
    """-> {module id: (entry, SPIR-V, [draw events])} of the frame's draws' "frag" / "vert" shaders."""
    out = {}
    for e, st in sorted(f.calls.items()):
        if stage in st["stages"] and "comp" not in st["stages"]:
            module, entry = st["stages"][stage]
            out.setdefault(module, (entry, f.modules[module], []))[2].append(e)
    return out


# no "varyings zeroed" knockout: the driver folds constant varyings into the fragment shader, which then costs less too
KNOCKOUTS = (("frag", "colour", rd_spirv.zero_outputs), ("vert", "draw", rd_spirv.off_screen))


def knockouts(f):
    """-> [(stage, mode, stub maker, module id, (entry, SPIR-V, [draw events]))] of every shader to swap in turn."""
    return [(stage, mode, make, module, v) for stage, mode, make in KNOCKOUTS
            for module, v in sorted(shaders_of(f, stage).items())]
