"""core/quads: fragment shader threads from rasterized triangles (synthetic geometry)."""
import unittest

try:
    import numpy as np
    from paretogpu.core import quads
except ImportError:  # numpy is optional for the other tests
    np = None

W, H = 8, 8


def clip(points, z=0.5):
    """pixel (x, y) -> clip-space (x, y, z, 1) for an 8x8 target and a full viewport."""
    return [[px / W * 2 - 1, py / H * 2 - 1, z, 1.0] for px, py in points]


def draw(eid, tris, offset, samples=1, zfunc="GreaterEqual", zwrite=True, cull="None"):
    return str(eid), {"tris": len(tris), "offset": offset, "viewport": [0, 0, W, H], "size": [W, H],
                      "samples": samples, "cull": cull, "front_ccw": True, "depth": 7, "ztest": True,
                      "zfunc": zfunc, "zwrite": zwrite}


@unittest.skipIf(np is None, "numpy is not installed")
class QuadsTest(unittest.TestCase):
    def run_draws(self, draws, actions=()):
        tris = np.array([t for _, ts, _ in draws for t in ts], dtype=np.float32)
        meta, off = {}, 0
        for eid, ts, kw in draws:
            k, m = draw(eid, ts, off, **kw)
            meta[k] = m
            off += len(ts)
        return quads.count(meta, tris, list(actions))

    def test_opaque_counts_what_is_left_at_the_end_of_its_pass(self):
        def square(x0, x1, z):
            return [clip([(x0, 0), (x1, 0), (x1, 8)], z), clip([(x0, 0), (x1, 8), (x0, 8)], z)]
        far, near = (10, square(0, 8, 0.2), {}), (20, square(0, 4, 0.8), {})
        r = self.run_draws([far, near])
        self.assertEqual((r["10"]["quads"], r["20"]["quads"]), (20, 12))  # quads on a diagonal run for both triangles
        tris = np.array(far[1] + near[1], dtype=np.float32)
        meta = dict([draw(10, far[1], 0), draw(20, near[1], 2)])
        v = quads.count(meta, tris, [], visible={"10", "20"})
        self.assertEqual((v["10"]["quads"], v["10"]["drawn"], v["10"]["samples"], v["20"]["quads"]), (10, 20, 64, 12))
        for fmt in ("D24S8", "D16"):  # the final depth is compared as stored: rounded to the format
            d = {k: {**x, "depth_format": fmt} for k, x in meta.items()}
            v = quads.count(d, tris + np.float32(1e-7), [], visible={"10", "20"})
            self.assertEqual((v["10"]["quads"], v["20"]["quads"]), (10, 12), fmt)
        passes = [{"event": 5, "name": "vkCmdBeginRenderPass(D=Load)"}, {"event": 15, "name": "vkCmdBeginRenderPass(D=Load)"}]
        v = quads.count(meta, tris, passes, visible={"10", "20"})
        self.assertEqual((v["10"]["quads"], v["20"]["quads"]), (20, 12))  # covered in a later pass: not killed

    def test_shared_edge_counts_once(self):
        square = [clip([(0, 0), (4, 0), (4, 4)]), clip([(0, 0), (4, 4), (0, 4)])]
        r = self.run_draws([(10, square, {})])["10"]
        # 16 pixels once each; the quads on the diagonal run for both triangles
        self.assertEqual((r["pixels"], r["quads"], r["samples"]), (16, 6, 16))

    def test_small_triangle_pays_its_whole_quad(self):
        r = self.run_draws([(10, [clip([(0, 0), (1.9, 0), (0, 1.9)])], {})])["10"]
        self.assertEqual((r["pixels"], r["quads"]), (1, 1))  # one pixel, four threads

    def test_viewport_and_scissor_bound_the_draw(self):
        tri = [clip([(-8, -8), (24, -8), (-8, 24)])]  # covers the whole target and more
        full = self.run_draws([(10, tri, {})])["10"]["pixels"]
        meta = {"10": {**draw(10, tri, 0)[1], "scissor": [0, 0, 4, 2]}}
        cut = quads.count(meta, np.array(tri, dtype=np.float32), [])["10"]["pixels"]
        self.assertEqual((full, cut), (64, 8))
        far = [clip([(0, 0), (8, 0), (0, 8)], 1.5)]  # beyond the far plane
        self.assertEqual(self.run_draws([(10, far, {})])["10"]["pixels"], 0)

    def test_a_triangle_through_the_near_plane_keeps_its_visible_part(self):
        # reversed Z: the near plane is z = w; one vertex beyond it (behind the eye side) is clipped, not the triangle
        tri = [[[-1, -1, 0.5, 1], [1, -1, 0.5, 1], [-1, 1, 1.5, 1]]]
        r = self.run_draws([(10, tri, {})])["10"]["pixels"]
        self.assertGreater(r, 0)
        self.assertLess(r, 32)

    def test_clipped_triangle_keeps_its_place_in_the_draw(self):
        # a near triangle through the near plane, then a farther one behind it: the farther one fails where they overlap
        near = [[-1, -1, 0.5, 1], [1, -1, 0.5, 1], [-1, 1, 1.5, 1]]
        far = clip([(0, 0), (8, 0), (0, 8)], 0.1)
        one = self.run_draws([(10, [near, far], {})])["10"]["samples"]
        two = self.run_draws([(10, [near], {}), (11, [far], {})])
        self.assertEqual(one, two["10"]["samples"] + two["11"]["samples"])

    def test_discarding_draw_stores_depth_only_where_written(self):
        near, far = clip([(0, 0), (8, 0), (8, 8)], 0.9), clip([(0, 0), (8, 0), (8, 8)], 0.1)
        tris = np.array([near, far], dtype=np.float32)
        meta = {"10": draw(10, [near], 0)[1], "11": draw(11, [far], 1)[1]}
        full = quads.count(meta, tris, [])["11"]["samples"]
        meta["10"]["written"] = np.zeros(W * H, bool)
        meta["10"]["written"][:W * H // 2] = True  # the lower half kept its fragments
        part = quads.count(meta, tris, [])
        self.assertEqual(full, 0)
        self.assertEqual(part["10"]["samples"], self.run_draws([(10, [near], {})])["10"]["samples"])
        alone = self.run_draws([(11, [far], {})])["11"]["samples"]
        lower = quads.count({"11": {**draw(11, [far], 0)[1], "scissor": [0, 0, W, H // 2]}},
                            np.array([far], dtype=np.float32), [])["11"]["samples"]
        self.assertEqual(part["11"]["samples"], alone - lower)
        self.assertGreater(alone - lower, 0)

    def test_depth_and_msaa(self):
        near, far = clip([(0, 0), (8, 0), (0, 8)], 0.9), clip([(0, 0), (8, 0), (0, 8)], 0.1)
        r = self.run_draws([(10, [near], {"samples": 4}), (11, [far], {"samples": 4})])
        self.assertGreater(r["10"]["samples"], 0)
        self.assertEqual(r["11"]["samples"], 0)  # behind with reversed Z (greater = closer)
        cleared = self.run_draws([(10, [near], {"samples": 4}), (12, [far], {"samples": 4})],
                                 [{"event": 11, "name": "vkCmdBeginRenderPass(C=Clear, D=Clear)"}])
        self.assertEqual(cleared["12"]["samples"], r["10"]["samples"])  # the pass cleared the depth


if __name__ == "__main__":
    unittest.main()
