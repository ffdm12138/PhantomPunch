import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adb_client as A
import config as C


class TestAdbClient(unittest.TestCase):
    def test_timeout_converted_to_adb_error(self):
        adb = A.Adb("adb.exe", "SER123", timeout=5)
        with patch("adb_client.subprocess.run", side_effect=subprocess.TimeoutExpired(["adb"], 5)), patch("adb_client.time.sleep"):
            with self.assertRaises(A.AdbError):
                adb.screencap()

    def test_kill_server_timeout_safe(self):
        adb = A.Adb("adb.exe", "SER123")
        with patch("adb_client.subprocess.run", side_effect=subprocess.TimeoutExpired(["adb", "kill-server"], 15)):
            adb.kill_server()

    def test_shell_device_not_found_raises(self):
        adb = A.Adb("adb.exe", "SER123")
        proc = subprocess.CompletedProcess(["adb"], 1, stdout=b"", stderr=b"error: device 'SER123' not found")
        with patch("adb_client.subprocess.run", return_value=proc):
            with self.assertRaises(A.AdbError):
                adb.shell("dumpsys power")

    def test_wait_online_tolerates_transient_unauthorized(self):
        adb = A.Adb("adb.exe", "SER123")
        with patch.object(adb, "device_state", side_effect=["unauthorized", "device"]), patch(
            "adb_client.time.sleep"
        ):
            self.assertEqual(adb.wait_online(10), "device")


class TestConfigValidation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.fake_adb = self.root / "adb.exe"
        self.fake_adb.write_text("dummy", encoding="utf-8")
        self.cfg_path = self.root / "config.json"

    def tearDown(self):
        self.dir.cleanup()

    def _base_raw(self):
        return {
            "adb_path": str(self.fake_adb),
            "serial": "SER123",
            "package": "com.alibaba.android.rimet",
            "probability_num": 5,
            "probability_den": 7,
            "exclude_ranges": {"2027": [["2027-02-05", "2027-02-13"]]},
            "match_threshold": 0.85,
            "deadline_s": 480,
            "device_wait_s": 60,
            "kill_adb_after": True,
            "log_retention_days": 30,
            "tap_offset_ratio": 0.25,
            "range_wait_s": 45,
            "settle_s": 5.0,
        }

    def test_valid_config_loads(self):
        self.cfg_path.write_text(json.dumps(self._base_raw()), encoding="utf-8")
        cfg = C.load_config(self.cfg_path)
        self.assertEqual(cfg.settle_s, 5.0)
        self.assertEqual(cfg.range_wait_s, 45.0)

    def test_invalid_settle_or_threshold_rejected(self):
        for key, bad in (("settle_s", -0.1), ("settle_s", 31), ("match_threshold", 0), ("deadline_s", 0)):
            raw = self._base_raw()
            raw[key] = bad
            self.cfg_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(C.ConfigError):
                C.load_config(self.cfg_path)


if __name__ == "__main__":
    unittest.main()
