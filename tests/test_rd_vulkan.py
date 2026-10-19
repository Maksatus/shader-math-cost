"""adapters/renderdoc/rd_vulkan.Frame: call state from structured chunks."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "paretogpu", "adapters", "renderdoc"))
import rd_vulkan  # noqa: E402

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

    def test_update_buffer(self):
        self.f.event(2, "vkCmdUpdateBuffer", {"destBuffer": 9, "destOffset": 4, "pData": b"\x01\x02"})
        self.assertEqual(self.f.read(9, 4, 2), b"\x01\x02")
        self.assertEqual(bytes(self.f.memory[3][12:14]), b"\x01\x02")


if __name__ == "__main__":
    unittest.main()
