import logging
import time

import vision as V

log = logging.getLogger(__name__)


class FlowError(Exception):
    pass


class SafetyAbort(Exception):
    pass


class EnvError(Exception):
    pass


class Flow:
    def __init__(self, adb, specs, cfg, dry_run, evidence_dir, deadline):
        self.adb = adb
        self.specs = specs
        self.cfg = cfg
        self.dry_run = dry_run
        self.evidence_dir = evidence_dir
        self.deadline = deadline
        self.woke = False
        self.last_evidence = None
        self._tpl = {}

    def tpl(self, name):
        if name not in self._tpl:
            self._tpl[name] = V.load_template(self.specs[name])
        return self._tpl[name]

    def hit(self, img, name):
        spec = self.specs.get(name)
        if spec is None:
            return None
        return V.match_one(img, self.tpl(name), spec.threshold, spec.roi)

    def hits(self, img, name):
        spec = self.specs.get(name)
        if spec is None:
            return []
        return V.match_all(img, self.tpl(name), spec.threshold, spec.roi)

    def check_deadline(self):
        if time.monotonic() > self.deadline:
            raise FlowError("deadline exceeded")

    def evidence(self, img, label):
        path = self.evidence_dir / f"{label}_{int(time.time())}.png"
        V.save_png(img, path)
        self.last_evidence = str(path)
        log.info("evidence saved: %s", path)
        return path

    def ensure_ready(self):
        st = self.adb.wait_online(self.cfg.device_wait_s)
        if st != "device":
            raise EnvError(f"device not usable: {st}")
        was_on = self.adb.screen_is_on()
        self.woke = was_on is False
        log.info("screen was_on=%s", was_on)
        self.adb.wake()
        time.sleep(0.8)
        w, h = self.adb.wm_size()
        self.adb.swipe(w // 2, int(h * 0.85), w // 2, int(h * 0.25), 300)
        time.sleep(0.8)
        self.adb.home()
        time.sleep(0.8)

    def _safety(self, img):
        if self.hit(img, "T14"):
            self.evidence(img, "safety_face")
            raise SafetyAbort("face/photo verification page detected")
        if self.hit(img, "T11"):
            self.evidence(img, "safety_range")
            raise SafetyAbort("out of attendance range")

    def goto_workbench(self):
        self.adb.launch_app(self.cfg.package)
        time.sleep(3)
        for _ in range(8):
            self.check_deadline()
            img = self.adb.screencap()
            self._safety(img)
            if self.hit(img, "T03") or self.hit(img, "T02"):
                return img
            tab = self.hit(img, "T01")
            if tab:
                self.adb.tap(tab[1], tab[2])
                time.sleep(2)
                continue
            time.sleep(2)
        self.evidence(img, "workbench_fail")
        raise FlowError("cannot reach workbench")

    def goto_attendance(self):
        self.adb.launch_app(self.cfg.package)
        time.sleep(3)
        unknown = 0
        scrolls = 0
        img = None
        for rnd in range(12):
            self.check_deadline()
            img = self.adb.screencap()
            self._safety(img)
            popup = self.hit(img, "T08") or self.hit(img, "T09")
            if popup:
                log.info("popup dismissed via template at %s", popup[1:])
                self.adb.tap(popup[1], popup[2])
                time.sleep(1.5)
                continue
            if self.hit(img, "T06"):
                log.info("success popup detected; BACK to dismiss")
                self.adb.back()
                time.sleep(1.5)
                continue
            if self.hit(img, "T12") or self.hit(img, "T10"):
                return img
            icons = self.hits(img, "T03")
            if icons:
                top = V.topmost(icons)
                log.info("tap attendance icon (topmost of %d) at %s", len(icons), top[1:])
                self.adb.tap(top[1], top[2])
                time.sleep(2.5)
                continue
            if self.hit(img, "T02"):
                if scrolls < 2:
                    w, h = self.adb.wm_size()
                    self.adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.35), 300)
                    scrolls += 1
                    time.sleep(1.2)
                    continue
                self.evidence(img, "icon_missing")
                raise FlowError("workbench visible but attendance icon not found")
            tab = self.hit(img, "T01")
            if tab:
                self.adb.tap(tab[1], tab[2])
                time.sleep(2)
                continue
            unknown += 1
            log.warning("unknown page round %d (streak %d)", rnd, unknown)
            if unknown >= 3:
                self.adb.back()
                unknown = 0
                time.sleep(1.5)
            else:
                time.sleep(2)
        if img is not None:
            self.evidence(img, "nav_fail")
        raise FlowError("navigation to attendance page failed")

    def read_mode(self, img):
        self._safety(img)
        if not self.hit(img, "T10"):
            self.evidence(img, "range_anchor_missing")
            raise SafetyAbort("in-range anchor missing, cannot confirm attendance range")
        if self.hit(img, "T04"):
            return "signin"
        if self.hit(img, "T05"):
            return "signout"
        self.evidence(img, "mode_unknown")
        raise FlowError("cannot read punch mode")

    def punch_and_verify(self, img, expected):
        mode = self.read_mode(img)
        log.info("punch mode=%s expected=%s", mode, expected)
        if mode != expected:
            if expected == "signin" and mode == "signout":
                log.info("already signed in manually; no tap")
                return "already"
            self.evidence(img, "mode_mismatch")
            raise SafetyAbort(f"unexpected mode {mode} for {expected}; no tap")
        if self.dry_run:
            log.info("dry-run: would tap %s button", expected)
            return "dry"
        h = self.hit(img, "T04" if expected == "signin" else "T05")
        self.adb.tap(h[1], h[2])
        log.info("tapped %s button at %s", expected, h[1:])
        flipped = "signout" if expected == "signin" else "signin"
        end = time.monotonic() + 20
        im2 = img
        while time.monotonic() < end:
            time.sleep(1.5)
            self.check_deadline()
            im2 = self.adb.screencap()
            if self.hit(im2, "T06"):
                self.evidence(im2, f"success_{expected}")
                return "ok"
            close = self.hit(im2, "T07")
            if close:
                self.adb.tap(close[1], close[2])
                time.sleep(1.0)
                im2 = self.adb.screencap()
            if self.hit(im2, "T06"):
                self.evidence(im2, f"success_{expected}")
                return "ok"
            tapped = "T04" if expected == "signin" else "T05"
            if not self.hit(im2, tapped) and self.hit(im2, "T12"):
                self.evidence(im2, f"success_{expected}")
                return "ok"
            try:
                m = self.read_mode(im2)
            except (FlowError, SafetyAbort):
                m = None
            if m == flipped:
                self.evidence(im2, f"success_{expected}")
                return "ok"
        self.evidence(im2, f"verify_timeout_{expected}")
        raise FlowError(f"{expected} punch not verified within 20s")

    def finish(self):
        try:
            self.adb.home()
            self.adb.shell(f"am force-stop {self.cfg.package}")
            log.info("dingtalk force-stopped (no background residue)")
            if self.woke:
                self.adb.power()
                log.info("screen restored to off")
        except Exception as e:
            log.warning("cleanup failed: %s", e)
        if self.cfg.kill_adb_after:
            try:
                self.adb.kill_server()
                log.info("adb server killed (zero residue)")
            except Exception as e:
                log.warning("kill-server failed: %s", e)