"""adapters/renderdoc/rd_vulkan.Frame: call state from structured chunks; rd_spirv.sampled."""
import os
import sys
import unittest
from array import array

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "paretogpu", "adapters", "renderdoc"))
import rd_spirv  # noqa: E402
import rd_vulkan  # noqa: E402


def spirv(*insts):
    words = [rd_spirv.MAGIC, 0x10000, 0, 100, 0]
    for op, *args in insts:
        words += [((len(args) + 1) << 16) | op, *args]
    return array("I", words).tobytes()


F, IMG, SMP, SIMG, P_IMG, P_SMP, P_SIMG = 1, 2, 3, 4, 5, 6, 7
TYPES = [(22, F, 32), (25, IMG, F, 1, 0, 0, 0, 1, 0), (26, SMP), (27, SIMG, IMG), (32, P_IMG, 0, IMG),
         (32, P_SMP, 0, SMP), (32, P_SIMG, 0, SIMG)]


def binding(var, s, b):
    return [(71, var, 34, s), (71, var, 33, b)]


# fragment: texture set 0 binding 0 + sampler binding 1 joined by OpSampledImage (as Unity), combined one at binding 2
FRAG = spirv(*binding(20, 0, 0), *binding(21, 0, 1), *binding(22, 0, 2), *TYPES, (59, P_IMG, 20, 0),
             (59, P_SMP, 21, 0), (59, P_SIMG, 22, 0), (61, IMG, 30, 20), (61, SMP, 31, 21), (86, SIMG, 32, 30, 31))
# vertex: a texel fetch of set 0 binding 3, no sampler
VERT = spirv(*binding(23, 0, 3), *TYPES, (59, P_IMG, 23, 0), (61, IMG, 33, 23), (95, F, 34, 33, 0))

PIPE = {"pStages": [{"stage": 1, "module": 7, "pName": "main"}, {"stage": 16, "module": 8, "pName": "main"}],
        "pColorBlendState": {"pAttachments": [{"blendEnable": 0, "colorWriteMask": 15}]},
        "pDepthStencilState": {"depthTestEnable": 1, "depthWriteEnable": 1, "depthCompareOp": 1},
        "pRasterizationState": {"depthBiasEnable": 0, "depthClampEnable": 0, "cullMode": 2, "frontFace": 0},
        "pDynamicState": {"pDynamicStates": []}, "pMultisampleState": {"rasterizationSamples": 1},
        "pViewportState": {"pViewports": [{"x": 0, "y": 0, "width": 8, "height": 8}],
                           "pScissors": [{"offset": {"x": 0, "y": 0}, "extent": {"width": 8, "height": 8}}]},
        "pInputAssemblyState": {"topology": 3}}


class FrameTest(unittest.TestCase):
    def setUp(self):
        f = self.f = rd_vulkan.Frame()
        f.interface = lambda module, entry: {"blocks": []}
        f.modules = {7: VERT, 8: FRAG}
        f.setup("vkCreateImageView", {"View": 1, "CreateInfo": {"image": 101}})
        f.setup("vkCreateImageView", {"View": 2, "CreateInfo": {"image": 102}})
        f.setup("vkCreateGraphicsPipelines", {"Pipeline": 5, "CreateInfo": PIPE})
        f.setup("vkCreateBuffer", {"Buffer": 9, "CreateInfo": {"size": 16}})
        f.setup("vkAllocateMemory", {"Memory": 3, "AllocateInfo": {"allocationSize": 32}})
        f.setup("vkBindBufferMemory", {"buffer": 9, "memory": 3, "memoryOffset": 8})
        f.event(1, "vkCmdBindPipeline", {"pipelineBindPoint": 0, "pipeline": 5})

    def test_dynamic_rendering_and_indirect_draw(self):
        f = self.f
        f.event(2, "vkCmdBeginRendering", {"pRenderingInfo": {
            "pColorAttachments": [{"imageView": 1, "resolveImageView": 0}], "pDepthAttachment": {"imageView": 2}}})
        f.event(3, "vkCmdDrawIndexedIndirect", {"buffer": 9, "offset": 0, "drawCount": 1, "stride": 20})
        f.event(4, "vkCmdEndRendering", {})
        st = f.calls[3]
        self.assertEqual((st["outs"], st["depth"], st["indexed"], st["unique_vertices"]), ([101], 102, True, None))

    def test_sampled_pairs(self):
        self.assertEqual(rd_spirv.sampled(FRAG), [((0, 0), (0, 1)), ((0, 2), (0, 2))])
        self.assertEqual(rd_spirv.sampled(VERT), [((0, 3), None)])

    def test_textures_of_a_draw(self):
        f = self.f
        for view, image in ((11, 201), (12, 202), (13, 203)):
            f.setup("vkCreateImageView", {"View": view, "CreateInfo": {"image": image}})
        f.setup("vkCreateSampler", {"Sampler": 41, "CreateInfo": {"minFilter": 1, "mipmapMode": 1, "maxLod": 1000.0,
                                                                  "anisotropyEnable": 1, "maxAnisotropy": 8.0}})
        f.setup("vkCreateSampler", {"Sampler": 42, "CreateInfo": {"minFilter": 0, "mipmapMode": 0, "maxLod": 0.0,
                                                                  "compareEnable": 1}})
        f.setup("vkCreateDescriptorSetLayout", {"SetLayout": 50, "CreateInfo": {"pBindings": [
            {"binding": b, "descriptorType": t, "descriptorCount": 1} for b, t in ((0, 2), (1, 0), (2, 1), (3, 2))]}})
        f.setup("vkAllocateDescriptorSets", {"DescriptorSet": 60, "AllocateInfo": {"pSetLayouts": [50]}})
        f.setup("Internal::Initial Contents", {"type": 19, "id": 60, "Bindings": [
            {"type": 2, "resource": 11}, {"type": 0, "sampler": 41, "resource": 0}, {"type": 1, "resource": 12,
                                                                                    "sampler": 42}, {}]})
        f.event(2, "vkUpdateDescriptorSets", {"pDescriptorWrites": [
            {"dstSet": 60, "dstBinding": 3, "dstArrayElement": 0, "descriptorCount": 1, "descriptorType": 2,
             "pBufferInfo": [], "pImageInfo": [{"sampler": 0, "imageView": 13, "imageLayout": 5}]}]})
        f.event(3, "vkCmdBindDescriptorSets", {"pipelineBindPoint": 0, "firstSet": 0, "pDescriptorSets": [60],
                                               "pDynamicOffsets": []})
        f.event(4, "vkCmdBeginRendering", {"pRenderingInfo": {"pColorAttachments": [{"imageView": 1,
                                                                                      "resolveImageView": 0}]}})
        f.event(5, "vkCmdDraw", {"vertexCount": 3, "instanceCount": 1})
        got = sorted((t["image"], t["stage"], t["filter"]) for t in f.calls[5]["textures"])
        self.assertEqual(got, [(201, "frag", "aniso 8x"), (202, "frag", "point + compare"), (203, "vert", None)])

    def test_update_buffer(self):
        self.f.event(2, "vkCmdUpdateBuffer", {"destBuffer": 9, "destOffset": 4, "pData": b"\x01\x02"})
        self.assertEqual(self.f.read(9, 4, 2), b"\x01\x02")
        self.assertEqual(bytes(self.f.memory[3][12:14]), b"\x01\x02")


if __name__ == "__main__":
    unittest.main()
