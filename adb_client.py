import logging
import re
import subprocess
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class AdbError(Exception):
    pass


class Adb:
    def __init__(self, adb_path, serial, timeout=20):
        self.adb_path = str(adb_path)
        self.serial = serial
        self.timeout = timeout

    def _run(self, args, timeout=None, with_serial=True):
        cmd = [self.adb_path] + (["-s", self.serial] if with_serial else []) + list(args)
        return subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout)

    def device_state(self) -> str:
        p = self._run(["devices"], with_serial=False)
        for line in p.stdout.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == self.serial:
                return parts[1]
        return "missing"

    def wait_online(self, timeout_s: int) -> str:
        end = time.monotonic() + timeout_s
        reconnected = False
        while time.monotonic() < end:
            st = self.device_state()
            if st == "device":
                return st
            if st == "offline" and not reconnected:
                self._run(["reconnect"])
                reconnected = True
            if st == "unauthorized":
                return st
            time.sleep(2)
        return self.device_state()

    def shell(self, cmd: str) -> str:
        p = self._run(["shell", cmd])
        return p.stdout.decode("utf-8", errors="replace")

    def screencap(self) -> np.ndarray:
        p = None
        for _ in range(2):
            p = self._run(["exec-out", "screencap", "-p"], timeout=25)
            buf = p.stdout
            if buf[:8] == PNG_MAGIC:
                img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    return img
        err = p.stderr.decode(errors="replace")[:200] if p is not None else ""
        raise AdbError(f"screencap failed: {err}")

    def tap(self, x, y):
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def keyevent(self, n):
        self.shell(f"input keyevent {n}")

    def wake(self):
        self.keyevent(224)

    def home(self):
        self.keyevent(3)

    def back(self):
        self.keyevent(4)

    def power(self):
        self.keyevent(26)

    def screen_is_on(self):
        out = self.shell("dumpsys power")
        m = re.search(r"mWakefulness=(Awake|Asleep|Dozing)", out)
        if m:
            return m.group(1) == "Awake"
        m = re.search(r"Display Power:\s*state=(ON|OFF)", out)
        if m:
            return m.group(1) == "ON"
        m = re.search(r"mScreenOn=(true|false)", out)
        if m:
            return m.group(1) == "true"
        return None

    def wm_size(self):
        out = self.shell("wm size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(
            r"Physical size:\s*(\d+)x(\d+)", out
        )
        if not m:
            raise AdbError(f"wm size unparsable: {out[:100]}")
        return int(m.group(1)), int(m.group(2))

    def launch_app(self, pkg):
        out = self.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
        if "No activities found" in out:
            raise AdbError(f"package not launchable: {pkg}")

    def package_installed(self, pkg) -> bool:
        return pkg in self.shell(f"pm list packages {pkg}")

    def kill_server(self):
        subprocess.run([self.adb_path, "kill-server"], capture_output=True, timeout=15)