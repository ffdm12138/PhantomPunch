import json
import tempfile
import time
import unittest
from pathlib import Path

import state as ST


class TestState(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "state.json"
        self.lock = Path(self.dir.name) / "state.lock"

    def tearDown(self):
        self.dir.cleanup()

    def test_missing(self):
        self.assertIsNone(ST.load(self.path))

    def test_roundtrip(self):
        st = ST.DayState(date="2026-11-18", decision=True, decision_reason="rolled", signin_done=True)
        ST.save_atomic(self.path, st)
        got = ST.load(self.path)
        self.assertEqual(got, st)
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_corrupt(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(ST.load(self.path))

    def test_partial_fields_default(self):
        self.path.write_text(json.dumps({"date": "2026-11-18"}), encoding="utf-8")
        got = ST.load(self.path)
        self.assertFalse(got.decision)
        self.assertFalse(got.signout_done)

    def test_lock_mutual_exclusion(self):
        with ST.acquire_lock(self.lock) as a:
            self.assertTrue(a)
            with ST.acquire_lock(self.lock) as b:
                self.assertFalse(b)
        self.assertFalse(self.lock.exists())

    def test_lock_stale_takeover(self):
        self.lock.write_text(json.dumps({"pid": 999999, "ts": time.time() - 9999}), encoding="utf-8")
        with ST.acquire_lock(self.lock, stale_s=600) as a:
            self.assertTrue(a)

    def test_ledger_append(self):
        p = Path(self.dir.name) / "records" / "punches.csv"
        ST.append_record(p, {"日期": "2026-11-18", "类型": "上班", "打卡时间": "t1", "来源": "auto", "结果": "ok", "证据": "e1"})
        ST.append_record(p, {"日期": "2026-11-18", "类型": "下班", "打卡时间": "t2", "来源": "auto", "结果": "ok", "证据": "e2"})
        lines = p.read_text(encoding="utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("日期", lines[0])
        self.assertIn("t2", lines[2])


if __name__ == "__main__":
    unittest.main()