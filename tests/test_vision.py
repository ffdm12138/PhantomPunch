import unittest

import cv2
import numpy as np

import vision as V


def synth():
    img = np.full((800, 400, 3), 30, np.uint8)
    for y0 in (100, 500):
        img[y0 : y0 + 40, 100:160] = 255
        img[y0 + 10 : y0 + 30, 115:145] = 0
    return img


class TestVision(unittest.TestCase):
    def setUp(self):
        self.img = synth()
        self.tpl = self.img[100:140, 100:160].copy()

    def test_self_match(self):
        hit = V.match_one(self.img, self.tpl, 0.85)
        self.assertIsNotNone(hit)
        self.assertGreaterEqual(hit[0], 0.99)
        self.assertEqual((hit[1], hit[2]), (130, 120))

    def test_nms_topmost(self):
        hits = V.match_all(self.img, self.tpl, 0.85)
        self.assertEqual(len(hits), 2)
        top = V.topmost(hits)
        self.assertEqual(top[2], 120)

    def test_scale_negative(self):
        small = cv2.resize(self.tpl, None, fx=0.8, fy=0.8)
        res = cv2.matchTemplate(
            cv2.cvtColor(self.img, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(small, cv2.COLOR_BGR2GRAY),
            cv2.TM_CCOEFF_NORMED,
        )
        self.assertLess(float(res.max()), 0.99)

    def test_roi_limits_search(self):
        hit = V.match_one(self.img, self.tpl, 0.85, roi=(0.0, 0.0, 1.0, 0.5))
        self.assertEqual(hit[2], 120)
        hits = V.match_all(self.img, self.tpl, 0.85, roi=(0.0, 0.0, 1.0, 0.5))
        self.assertEqual(len(hits), 1)

    def test_absent(self):
        blank = np.full((800, 400, 3), 30, np.uint8)
        self.assertIsNone(V.match_one(blank, self.tpl, 0.85))

    def test_constant_template_rejected(self):
        flat = np.full((40, 60, 3), 255, np.uint8)
        self.assertIsNone(V.match_one(self.img, flat, 0.85))
        self.assertEqual(V.match_all(self.img, flat, 0.85), [])


if __name__ == "__main__":
    unittest.main()