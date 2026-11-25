import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

import flow as F
import vision as V


def _pattern_tile(w, h, step, phase=0):
    tile = np.full((h, w, 3), 245, np.uint8)
    for y in range(0, h, step):
        for x in range(0, w, step):
            if ((x // step + y // step + phase) % 2) == 0:
                tile[y : y + step, x : x + step] = (30, 30, 30)
    return tile


class _FakeClock:
    def __init__(self, step=0.6):
        self.t = 100.0
        self.step = step

    def monotonic(self):
        return self.t

    def sleep(self, secs):
        self.t += max(secs, self.step)


class _FakeAdb:
    def __init__(self, frames, screen_on_seq=None):
        self.frames = list(frames)
        self.idx = 0
        self.taps = []
        self.powers = 0
        self.homes = 0
        self.killed = 0
        self.shells = []
        self.screen_on_seq = list(screen_on_seq or [True])

    def screencap(self):
        img = self.frames[min(self.idx, len(self.frames) - 1)]
        self.idx += 1
        return img

    def tap(self, x, y):
        self.taps.append((x, y))

    def home(self):
        self.homes += 1

    def power(self):
        self.powers += 1

    def shell(self, cmd):
        self.shells.append(cmd)
        return ""

    def screen_is_on(self):
        if len(self.screen_on_seq) > 1:
            return self.screen_on_seq.pop(0)
        return self.screen_on_seq[0]

    def kill_server(self):
        self.killed += 1


class TestFlowSettleAndSafety(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.tdir = root / "templates"
        self.tdir.mkdir()
        self.ev_dir = root / "evidence"
        self.ev_dir.mkdir()

        self.t10 = _pattern_tile(140, 40, 7, phase=0)
        self.t14 = _pattern_tile(140, 40, 11, phase=1)
        self.t07 = _pattern_tile(140, 40, 13, phase=0)
        self.t08 = _pattern_tile(60, 60, 5, phase=1)
        for name, img in (("T10", self.t10), ("T14", self.t14), ("T07", self.t07), ("T08", self.t08)):
            cv2.imwrite(str(self.tdir / f"{name}.png"), img)
        self.specs = {
            name: V.Spec(name=name, path=self.tdir / f"{name}.png", threshold=0.9)
            for name in ("T10", "T14", "T07", "T08")
        }
        self.cfg = SimpleNamespace(
            package="com.alibaba.android.rimet",
            range_wait_s=6.0,
            settle_s=1.5,
            tap_offset_ratio=0.25,
            kill_adb_after=True,
        )

    def tearDown(self):
        self.dir.cleanup()

    def _frame(self, blue_y=None, has_t10=True, has_t14=False, has_t07=False, has_t08=False):
        img = np.full((2232, 1080, 3), 245, np.uint8)
        if blue_y is not None:
            cv2.circle(img, (539, blue_y), 230, (255, 119, 24), -1)
        if has_t10:
            img[1700:1740, 470:610] = self.t10
        if has_t14:
            img[500:540, 470:610] = self.t14
        if has_t07:
            img[1650:1690, 470:610] = self.t07
        if has_t08:
            img[250:310, 900:960] = self.t08
        return img

    def test_layout_shift_waits_for_settle_before_tap(self):
        clock = _FakeClock()
        f0 = self._frame(blue_y=994, has_t10=True)
        f1 = self._frame(blue_y=1388, has_t10=True)
        f2 = self._frame(blue_y=1388, has_t10=True)
        f3 = self._frame(blue_y=None, has_t10=False, has_t07=True)
        adb = _FakeAdb([f1, f2, f3])
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=False, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            res = flow.punch_and_verify(f0, "signin")
        self.assertEqual(res, "ok")
        self.assertEqual(len(adb.taps), 1)
        tx, ty = adb.taps[0]
        self.assertLess(abs(tx - 539), 70)
        self.assertLess(abs(ty - 1388), 70)

    def test_layout_unstable_aborts_without_tap(self):
        clock = _FakeClock(step=2.0)
        frames = [self._frame(blue_y=900 if i % 2 == 0 else 1350, has_t10=True) for i in range(10)]
        adb = _FakeAdb(frames[1:])
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=False, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            with self.assertRaises(F.FlowError):
                flow.punch_and_verify(frames[0], "signin")
        self.assertEqual(adb.taps, [])
        self.assertTrue(any("layout_unstable" in p.name for p in self.ev_dir.glob("*.png")))

    def test_locating_then_shift_then_settle(self):
        clock = _FakeClock()
        f0 = self._frame(blue_y=994, has_t10=False)
        f1 = self._frame(blue_y=994, has_t10=True)
        f2 = self._frame(blue_y=1388, has_t10=True)
        f3 = self._frame(blue_y=1388, has_t10=True)
        adb = _FakeAdb([f1, f2, f3])
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=True, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            res = flow.punch_and_verify(f0, "signin")
        self.assertEqual(res, "dry")
        self.assertEqual(adb.taps, [])
        self.assertEqual(flow.last_button[2], 1388)

    def test_missing_anchor_raises_safety_abort_no_tap(self):
        clock = _FakeClock(step=2.0)
        frames = [self._frame(blue_y=994, has_t10=False) for _ in range(6)]
        adb = _FakeAdb(frames[1:])
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=False, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            with self.assertRaises(F.SafetyAbort):
                flow.punch_and_verify(frames[0], "signin")
        self.assertEqual(adb.taps, [])

    def test_safety_triggered_during_settle(self):
        clock = _FakeClock()
        f0 = self._frame(blue_y=994, has_t10=True)
        f1 = self._frame(blue_y=994, has_t10=True, has_t14=True)
        adb = _FakeAdb([f1])
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=False, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            with self.assertRaises(F.SafetyAbort):
                flow.punch_and_verify(f0, "signin")
        self.assertEqual(adb.taps, [])

    def test_popup_dismissed_resets_settle(self):
        clock = _FakeClock()
        f0 = self._frame(blue_y=1388, has_t10=True)
        f1 = self._frame(blue_y=1388, has_t10=True, has_t08=True)
        f2 = self._frame(blue_y=1388, has_t10=True)
        f3 = self._frame(blue_y=1388, has_t10=True)
        adb = _FakeAdb([f1, f2, f3])
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=True, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            res = flow.punch_and_verify(f0, "signin")
        self.assertEqual(res, "dry")
        self.assertEqual(len(adb.taps), 1)
        self.assertLess(abs(adb.taps[0][0] - 930), 10)
        self.assertLess(abs(adb.taps[0][1] - 280), 10)

    def test_verify_timeout_reports_25s(self):
        clock = _FakeClock(step=5.0)
        f0 = self._frame(blue_y=1388, has_t10=True)
        frames = [self._frame(blue_y=1388, has_t10=True) for _ in range(10)]
        adb = _FakeAdb(frames)
        flow = F.Flow(adb, self.specs, self.cfg, dry_run=False, evidence_dir=self.ev_dir, deadline=500.0)
        with patch("flow.time.monotonic", clock.monotonic), patch("flow.time.sleep", clock.sleep):
            with self.assertRaises(F.FlowError) as ctx:
                flow.punch_and_verify(f0, "signin")
        self.assertIn("25s", str(ctx.exception))

    def test_finish_does_not_wake_already_off_screen(self):
        adb_off = _FakeAdb([], screen_on_seq=[False])
        flow_off = F.Flow(adb_off, self.specs, self.cfg, dry_run=True, evidence_dir=self.ev_dir, deadline=500.0)
        flow_off.woke = True
        flow_off.finish()
        self.assertEqual(adb_off.powers, 0)
        self.assertEqual(adb_off.killed, 1)

        adb_on = _FakeAdb([], screen_on_seq=[True])
        flow_on = F.Flow(adb_on, self.specs, self.cfg, dry_run=True, evidence_dir=self.ev_dir, deadline=500.0)
        flow_on.woke = True
        flow_on.finish()
        self.assertEqual(adb_on.powers, 1)


if __name__ == "__main__":
    unittest.main()
