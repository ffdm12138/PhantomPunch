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

    def test_find_blue_button(self):
        img = np.full((2232, 1080, 3), 40, np.uint8)
        cv2.circle(img, (540, 1300), 230, (255, 119, 24), -1)
        btn = V.find_blue_button(img)
        self.assertIsNotNone(btn)
        _, cx, cy, r = btn
        self.assertLess(abs(cx - 540), 15)
        self.assertLess(abs(cy - 1300), 15)
        self.assertLess(abs(r - 230), 15)

    def test_find_blue_button_absent(self):
        img = np.full((2232, 1080, 3), 40, np.uint8)
        self.assertIsNone(V.find_blue_button(img))
        small = img.copy()
        cv2.rectangle(small, (40, 300), (140, 400), (255, 119, 24), -1)
        self.assertIsNone(V.find_blue_button(small))

    def test_match_one_scales(self):
        img = np.full((400, 300, 3), 30, np.uint8)
        img[150:200, 120:180] = 255
        img[165:185, 135:165] = 0
        tpl = img[150:200, 120:180].copy()
        shrunk = cv2.resize(tpl, None, fx=0.9, fy=0.9)
        hit = V.match_one_scales(img, shrunk, 0.85)
        self.assertIsNotNone(hit)
        self.assertLess(abs(hit[1] - 150), 15)
        self.assertLess(abs(hit[2] - 175), 15)


if __name__ == "__main__":
    unittest.main()