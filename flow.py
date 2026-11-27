import logging
import random
import time

import vision as V

log = logging.getLogger(__name__)


class FlowError(Exception):
    pass


class SafetyAbort(Exception):
    pass


class EnvError(Exception):
    pass


MODE_FLOOR = 0.70
MODE_MARGIN = 0.08
SETTLE_HOLD_S = 5.0
SETTLE_SAMPLE_S = 0.25
VERIFY_WAIT_S = 40.0
PROGRESS_LOG_S = 10.0


def decide_mode(score04: float, score05: float):
    """共享"打卡"字保证底线分；靠 上/下 的相对差距判模式，不依赖绝对高分。"""
    best, other = max(score04, score05), min(score04, score05)
    if best < MODE_FLOOR or (best - other) < MODE_MARGIN:
        return None
    return "signin" if score04 > score05 else "signout"


def jitter_point(x, y, radius, ratio, rng=None):
    """偏移幅度与按钮半径成正比：按钮小偏得少，按钮大偏得多。"""
    r = rng if rng is not None else random
    return (
        int(round(x + r.uniform(-ratio, ratio) * radius)),
        int(round(y + r.uniform(-ratio, ratio) * radius)),
    )


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
        self.last_button = None
        self._last_fallback = None
        self._tpl = {}

    def tpl(self, name):
        if name not in self._tpl:
            self._tpl[name] = V.load_template(self.specs[name])
        return self._tpl[name]

    def hit(self, img, name):
        spec = self.specs.get(name)
        if spec is None:
            return None
        return V.match_one_scales(img, self.tpl(name), spec.threshold, spec.roi)

    def hits(self, img, name):
        spec = self.specs.get(name)
        if spec is None:
            return []
        return V.match_all_scales(img, self.tpl(name), spec.threshold, spec.roi)

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
        if self.adb.screen_is_on() is False:
            log.warning("screen still off after wake; retrying once")
            self.adb.wake()
            time.sleep(0.8)
        w, h = self.adb.wm_size()
        self.screen_size = (w, h)
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

    def _dismiss_popup(self, img):
        popup = self.hit(img, "T08") or self.hit(img, "T09")
        if not popup:
            return False
        log.info("popup dismissed via template at %s", popup[1:])
        self.adb.tap(popup[1], popup[2])
        time.sleep(1.5)
        return True

    def goto_workbench(self):
        self.adb.launch_app(self.cfg.package)
        time.sleep(3)
        img = None
        for _ in range(12):
            self.check_deadline()
            img = self.adb.screencap()
            self._safety(img)
            if self.hit(img, "T12") or V.find_blue_button(img):
                log.info("already on attendance page; BACK to workbench")
                self.adb.back()
                time.sleep(1.5)
                continue
            if self.hit(img, "T03") or self.hit(img, "T02"):
                return img
            tab = self.hit(img, "T01")
            if tab:
                self.adb.tap(tab[1], tab[2])
                time.sleep(2)
                continue
            time.sleep(2)
        if img is not None:
            self.evidence(img, "workbench_fail")
        raise FlowError("cannot reach workbench")

    def goto_attendance(self):
        self.adb.shell(f"am force-stop {self.cfg.package}")
        time.sleep(0.5)
        self.adb.launch_app(self.cfg.package)
        time.sleep(3)
        unknown = 0
        scrolls = 0
        img = None
        for rnd in range(20):
            self.check_deadline()
            img = self.adb.screencap()
            self._safety(img)
            if self._dismiss_popup(img):
                continue
            if self.hit(img, "T06"):
                log.info("success popup detected; BACK to dismiss")
                self.adb.back()
                time.sleep(1.5)
                continue
            if self.hit(img, "T12") or self.hit(img, "T10") or V.find_blue_button(img):
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

    def _button_anchor(self, img):
        """定位蓝色圆钮或文字降级位置，并计算 T04/T05 参考分。"""
        btn = V.find_blue_button(img)
        self.last_button = btn
        roi = None
        if btn:
            _, cx, cy, r = btn
            h, w = img.shape[:2]
            pad = int(r * 1.35)
            roi = (
                max(0.0, (cx - pad) / w),
                max(0.0, (cy - pad) / h),
                min(1.0, (cx + pad) / w),
                min(1.0, (cy + pad) / h),
            )
        scores, hits = {}, {}
        for name in ("T04", "T05"):
            spec = self.specs.get(name)
            h_hit = V.match_one_scales(img, self.tpl(name), 0.0, roi) if spec is not None else None
            scores[name] = h_hit[0] if h_hit else 0.0
            hits[name] = h_hit
        if btn:
            self._last_fallback = None
            return (btn[1], btn[2]), max(15, int(btn[3] * 0.08)), scores
        best_name = "T04" if scores["T04"] >= scores["T05"] else "T05"
        best_hit = hits[best_name]
        if best_hit and best_hit[0] >= 0.60:
            proxy_r = max(8, self.tpl(best_name).shape[1] // 4)
            self._last_fallback = (best_hit[1], best_hit[2], proxy_r)
            return (best_hit[1], best_hit[2]), 15, scores
        self._last_fallback = None
        return None, 15, scores

    def locate_button(self, img, anchor_wait=0.0, settle_s=None, button_wait=None):
        """按钮位置需在连续 settle 秒内始终稳定才返回；慢设备可一直等，直到整次运行的总时限。"""
        hold = getattr(self.cfg, "settle_s", SETTLE_HOLD_S) if settle_s is None else settle_s
        need_settle = anchor_wait > 0 and hold > 0
        end = time.monotonic() + max(0.0, anchor_wait)
        btn_end = None if button_wait is None else time.monotonic() + max(0.0, button_wait)
        stable_since = None
        ref_pos = None
        settle_started = None
        last_note = None
        scores = {"T04": 0.0, "T05": 0.0}

        while True:
            self._safety(img)
            if self._dismiss_popup(img):
                stable_since = ref_pos = None
                self.check_deadline()
                img = self.adb.screencap()
                continue

            if not self.hit(img, "T10"):
                stable_since = ref_pos = None
                if time.monotonic() >= end:
                    self.evidence(img, "range_anchor_missing")
                    raise SafetyAbort("in-range anchor missing, cannot confirm attendance range")
                log.info("attendance range not settled yet (locating?); waiting up to %.0fs", max(0.0, end - time.monotonic()))
                time.sleep(min(2.0, max(0.2, end - time.monotonic())))
                self.check_deadline()
                img = self.adb.screencap()
                continue

            pos, tol, scores = self._button_anchor(img)
            if pos is None:
                stable_since = ref_pos = None
                now = time.monotonic()
                if btn_end is not None and now >= btn_end:
                    break
                if last_note is None or now - last_note >= PROGRESS_LOG_S:
                    log.info("in-range anchor present, punch button not ready; continuing to wait (slow/laggy page)")
                    last_note = now
                time.sleep(1.5)
                self.check_deadline()
                img = self.adb.screencap()
                continue

            if not need_settle:
                break

            now = time.monotonic()
            if settle_started is None:
                settle_started = now
                last_note = now
            if ref_pos is None or abs(pos[0] - ref_pos[0]) > tol or abs(pos[1] - ref_pos[1]) > tol:
                if ref_pos is not None:
                    log.info("attendance button moved %s -> %s; restarting %.1fs stability window", ref_pos, pos, hold)
                else:
                    log.info("button candidate at %s; holding stable for %.1fs", pos, hold)
                ref_pos = pos
                stable_since = now
            elif now - stable_since >= hold:
                log.info("attendance layout settled at %s (stable %.1fs, tol=%d)", pos, now - stable_since, tol)
                break

            if now - last_note >= PROGRESS_LOG_S:
                log.info("layout still stabilizing (%.0fs since first candidate); continuing to wait", now - settle_started)
                last_note = now
            if time.monotonic() > self.deadline:
                self.evidence(img, "settle_deadline")
                raise FlowError("deadline exceeded while waiting for a stable layout")
            time.sleep(SETTLE_SAMPLE_S)
            img = self.adb.screencap()

        if self.last_button:
            _, cx, cy, r = self.last_button
            log.info("blue button found at (%d,%d) r=%d score=%.2f", cx, cy, r, self.last_button[0])
        elif self._last_fallback:
            log.info("text fallback button found at (%d,%d) r=%d", *self._last_fallback)
        log.info("mode scores T04=%.3f T05=%.3f (informational)", scores["T04"], scores["T05"])
        return img, scores

    def read_mode(self, img, anchor_wait=0.0):
        img, scores = self.locate_button(img, anchor_wait, button_wait=20.0)
        mode = decide_mode(scores["T04"], scores["T05"])
        if mode:
            return mode
        self.evidence(img, "mode_unknown")
        raise FlowError(f"punch mode ambiguous (T04={scores['T04']:.2f} T05={scores['T05']:.2f})")

    def _target_tap(self):
        """根据当前锚点计算随机落点，并钳制在屏幕边界内。"""
        if self.last_button:
            _, cx, cy, base_r = self.last_button
            kind = "punch button"
        else:
            cx, cy, base_r = self._last_fallback
            kind = "text fallback"
        tx, ty = jitter_point(cx, cy, base_r, self.cfg.tap_offset_ratio)
        size = getattr(self, "screen_size", None)
        if size:
            w, h = size
            tx = min(max(tx, 8), w - 8)
            ty = min(max(ty, 8), h - 8)
        log.info("%s center (%d,%d) r=%d -> target tap (%d,%d)", kind, cx, cy, base_r, tx, ty)
        return tx, ty

    def _verify_success(self, expected):
        end = time.monotonic() + VERIFY_WAIT_S
        im2 = None
        while time.monotonic() < end:
            time.sleep(1.5)
            self.check_deadline()
            im2 = self.adb.screencap()
            self._safety(im2)
            if self.hit(im2, "T07") or self.hit(im2, "T06"):
                time.sleep(1.0)
                im2 = self.adb.screencap()
                self.evidence(im2, f"success_{expected}")
                return "ok"
        if im2 is not None:
            self.evidence(im2, f"verify_timeout_{expected}")
        raise FlowError(f"{expected} punch not verified within {int(VERIFY_WAIT_S)}s")

    def punch_and_verify(self, img, expected):
        """只按时间窗口打卡：上下班文案仅记日志，不作为点击门槛。"""
        img, scores = self.locate_button(img, anchor_wait=self.cfg.range_wait_s)
        if not self.last_button and not self._last_fallback:
            self.evidence(img, "button_missing")
            raise FlowError("punch button not located")
        mode = decide_mode(scores["T04"], scores["T05"])
        log.info("punch window=%s observed label=%s (display is not a gate)", expected, mode or "ambiguous")
        tx, ty = self._target_tap()
        if self.dry_run:
            log.info("dry-run: would tap %s button at (%d,%d)", expected, tx, ty)
            return "dry"
        self.adb.tap(tx, ty)
        log.info("tapped %s button at (%d,%d)", expected, tx, ty)
        try:
            return self._verify_success(expected)
        except FlowError:
            pass
        late = self.adb.screencap()
        if self.hit(late, "T07") or self.hit(late, "T06"):
            time.sleep(1.0)
            late = self.adb.screencap()
            self.evidence(late, f"success_{expected}")
            return "ok"
        self.evidence(late, f"verify_retry_{expected}")
        log.warning("verify timeout; re-locating for one controlled retry")
        self.check_deadline()
        img2, _ = self.locate_button(late, anchor_wait=self.cfg.range_wait_s)
        if self.hit(img2, "T07") or self.hit(img2, "T06"):
            self.evidence(img2, f"success_{expected}")
            return "ok"
        if not self.last_button and not self._last_fallback:
            raise FlowError(f"{expected} punch not verified; punch button gone on retry")
        tx, ty = self._target_tap()
        self.adb.tap(tx, ty)
        log.info("retry: tapped %s button again at (%d,%d)", expected, tx, ty)
        return self._verify_success(expected)

    def finish(self):
        try:
            self.adb.home()
            self.adb.shell(f"am force-stop {self.cfg.package}")
            log.info("dingtalk force-stopped (no background residue)")
            if self.woke and self.adb.screen_is_on() is True:
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