import argparse
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import cv2

import scheduling as S
import state as ST
import vision as V
from adb_client import Adb, AdbError
from config import ConfigError, ROOT, load_config
from flow import EnvError, Flow, FlowError, SafetyAbort, decide_mode

log = logging.getLogger("autosign")


def setup_logging(cfg):
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - cfg.log_retention_days * 86400
    for p in cfg.logs_dir.glob("*.log"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(cfg.logs_dir / f"{date.today().isoformat()}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)


def make_flow(cfg, adb, dry_run):
    specs = V.load_manifest(cfg.templates_dir)
    evidence = cfg.logs_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return Flow(adb, specs, cfg, dry_run, evidence, time.monotonic() + cfg.deadline_s)


def cmd_signin(cfg, args):
    today = date.today()
    with ST.acquire_lock(cfg.lock_path) as owned:
        if not owned:
            log.error("another run holds the lock")
            return 5
        st = ST.load(cfg.state_path)
        excluded = S.is_excluded(today, cfg.exclude_ranges)
        action, decision, reason = S.signin_plan(
            st, today, excluded, args.force, cfg.probability_num, cfg.probability_den
        )
        if st is None or st.date != today.isoformat():
            st = ST.DayState(date=today.isoformat())
        st.decision = decision
        st.decision_reason = reason
        if not args.dry_run:
            ST.save_atomic(cfg.state_path, st)
        log.info("signin gate: action=%s decision=%s reason=%s excluded=%s", action, decision, reason, excluded)
        if action == "skip":
            return 0
        adb = Adb(cfg.adb_path, cfg.serial)
        flow = make_flow(cfg, adb, args.dry_run)
        try:
            flow.ensure_ready()
            img = flow.goto_attendance()
            result = flow.punch_and_verify(img, "signin")
            if not args.dry_run:
                st.signin_done = True
                st.signin_at = ST.now_iso()
                st.signin_source = "auto"
                ST.save_atomic(cfg.state_path, st)
                ST.append_record(cfg.records_path, {
                    "日期": st.date, "类型": "上班", "打卡时间": st.signin_at,
                    "来源": st.signin_source, "结果": result, "证据": flow.last_evidence or "",
                })
            log.info("signin finished: %s", result)
            return 0
        finally:
            flow.finish()


def cmd_signout(cfg, args):
    today = date.today()
    with ST.acquire_lock(cfg.lock_path) as owned:
        if not owned:
            log.error("another run holds the lock")
            return 5
        st = ST.load(cfg.state_path)
        action, reason = S.signout_gate(st, today, args.force)
        log.info("signout gate: action=%s reason=%s", action, reason)
        if action == "skip":
            return 0
        if st is None or st.date != today.isoformat():
            st = ST.DayState(date=today.isoformat(), decision=True, decision_reason="forced")
        adb = Adb(cfg.adb_path, cfg.serial)
        flow = make_flow(cfg, adb, args.dry_run)
        try:
            flow.ensure_ready()
            img = flow.goto_attendance()
            result = flow.punch_and_verify(img, "signout")
            if not args.dry_run:
                st.signout_done = True
                st.signout_at = ST.now_iso()
                ST.save_atomic(cfg.state_path, st)
                ST.append_record(cfg.records_path, {
                    "日期": st.date, "类型": "下班", "打卡时间": st.signout_at,
                    "来源": "auto", "结果": result, "证据": flow.last_evidence or "",
                })
            log.info("signout finished: %s", result)
            return 0
        finally:
            flow.finish()


def cmd_doctor(cfg, args):
    with ST.acquire_lock(cfg.lock_path) as owned:
        if not owned:
            log.error("another run holds the lock")
            return 5
        ok = True

        def check(name, good, detail=""):
            nonlocal ok
            ok = ok and good
            print(f"[{'OK' if good else 'FAIL'}] {name} {detail}")

        check("config", True, str(ROOT / "config.json"))
        check("adb binary", cfg.adb_path.is_file(), str(cfg.adb_path))
        adb = Adb(cfg.adb_path, cfg.serial)
        st = adb.wait_online(10)
        check("device online", st == "device", f"serial={cfg.serial} state={st}")
        if st == "device":
            try:
                w, h = adb.wm_size()
                check("wm size", True, f"{w}x{h}")
            except AdbError as e:
                check("wm size", False, str(e))
            check("dingtalk installed", adb.package_installed(cfg.package), cfg.package)
            on = adb.screen_is_on()
            check("screen state readable", on is not None, f"on={on}")
        off = datetime.now().astimezone().utcoffset()
        check("timezone UTC+8", off is not None and off.total_seconds() == 8 * 3600, str(off))
        years = {date.today().year, date.today().year + 1}
        missing = years - set(cfg.exclude_ranges)
        if missing:
            print(f"[WARN] exclude years missing={sorted(missing)} (update config.json after official holiday announcement)")
        else:
            check("exclude years covered", True, f"years={sorted(cfg.exclude_ranges)}")
        specs = V.load_manifest(cfg.templates_dir)
        if not specs:
            print("[WARN] no templates yet (phase-02 pending)")
        else:
            check("templates manifest", True, f"{len(specs)} templates")
        for name, spec in specs.items():
            check(f"template {name}", spec.path.is_file(), str(spec.path))
        if cfg.kill_adb_after:
            adb.kill_server()
        return 0 if ok else 3


def cmd_capture(cfg, args):
    with ST.acquire_lock(cfg.lock_path) as owned:
        if not owned:
            log.error("another run holds the lock")
            return 5
        adb = Adb(cfg.adb_path, cfg.serial)
        flow = make_flow(cfg, adb, dry_run=True)
        try:
            flow.ensure_ready()
            if args.page == "home":
                img = adb.screencap()
            elif args.page == "workbench":
                img = flow.goto_workbench()
            else:
                img = flow.goto_attendance()
            cfg.captures_dir.mkdir(parents=True, exist_ok=True)
            path = cfg.captures_dir / f"{args.label}.png"
            V.save_png(img, path)
            print(f"saved {path} shape={img.shape}")
            return 0
        finally:
            flow.finish()


def cmd_crop(cfg, args):
    src_path = cfg.captures_dir / args.src
    src = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
    if src is None:
        print(f"capture unreadable: {src_path}")
        return 3
    x1, y1, x2, y2 = (int(v) for v in args.box.split(","))
    tpl = src[y1:y2, x1:x2]
    if tpl.size == 0:
        print("empty crop box")
        return 3
    cfg.templates_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{args.name}.png"
    V.save_png(tpl, cfg.templates_dir / file_name)
    mf_path = cfg.templates_dir / "manifest.json"
    mf = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.is_file() else {"templates": []}
    entry = {
        "name": args.name,
        "file": file_name,
        "threshold": args.threshold,
        "roi": json.loads(args.roi) if args.roi else None,
        "source": args.src,
        "status": "ok",
    }
    mf["templates"] = [t for t in mf["templates"] if t["name"] != args.name] + [entry]
    mf_path.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"template {args.name} cropped {tpl.shape[1]}x{tpl.shape[0]} from {args.src}")
    return 0


def cmd_tour(cfg, args):
    with ST.acquire_lock(cfg.lock_path) as owned:
        if not owned:
            log.error("another run holds the lock")
            return 5
        adb = Adb(cfg.adb_path, cfg.serial)
        flow = make_flow(cfg, adb, dry_run=True)
        try:
            flow.ensure_ready()
            adb.shell(f"am force-stop {cfg.package}")
            time.sleep(1.5)
            img = flow.goto_workbench()
            log.info("TOUR: workbench reached (small tab/icon navigation exercised)")
            icons = flow.hits(img, "T03")
            if not icons:
                raise FlowError("tour: attendance icon not found on workbench")
            top = V.topmost(icons)
            log.info("TOUR: tap attendance icon at %s", top[1:])
            adb.tap(top[1], top[2])
            time.sleep(2.5)
            img2 = adb.screencap()
            _, scores = flow.locate_button(img2, anchor_wait=cfg.range_wait_s)
            if not flow.last_button and max(scores["T04"], scores["T05"]) < 0.60:
                raise FlowError("tour: punch button not located on attendance page")
            label = decide_mode(scores["T04"], scores["T05"]) or "ambiguous"
            log.info("TOUR: attendance page ok, observed label=%s", label)
            adb.back()
            time.sleep(2)
            img3 = adb.screencap()
            back_ok = bool(flow.hit(img3, "T03") or flow.hit(img3, "T02"))
            log.info("TOUR: back to workbench=%s", back_ok)
            if not back_ok:
                raise FlowError("tour: back navigation failed")
            log.info("TOUR: all navigation steps OK (zero punch taps)")
            return 0
        finally:
            flow.finish()


def cmd_selftest(cfg, args):
    specs = V.load_manifest(cfg.templates_dir)
    if not specs:
        print("no templates in manifest")
        return 2
    ok = True
    for name, spec in specs.items():
        src = cv2.imread(str(cfg.captures_dir / (spec.source or "")), cv2.IMREAD_COLOR)
        if src is None:
            print(f"[SKIP] {name}: source capture missing ({spec.source})")
            ok = False
            continue
        tpl = V.load_template(spec)
        hit = V.match_one(src, tpl, 0.0, spec.roi)
        score = hit[0] if hit else 0.0
        good = score >= max(spec.threshold, 0.99)
        ok = ok and good
        print(f"[{'OK' if good else 'FAIL'}] {name} score={score:.4f} threshold={spec.threshold}")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(prog="autosign")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("signin", "signout"):
        p = sub.add_parser(name)
        p.add_argument("--force", action="store_true")
        p.add_argument("--dry-run", action="store_true")
    sub.add_parser("doctor")
    p = sub.add_parser("capture")
    p.add_argument("--label", required=True)
    p.add_argument("--page", choices=["home", "workbench", "attendance"], default="attendance")
    p = sub.add_parser("crop")
    p.add_argument("--src", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--box", required=True, help="x1,y1,x2,y2 in capture pixels")
    p.add_argument("--threshold", type=float, default=0.85)
    p.add_argument("--roi", default=None, help="JSON [x1,y1,x2,y2] fractions")
    sub.add_parser("selftest")
    sub.add_parser("tour")
    args = ap.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}")
        return 3
    setup_logging(cfg)
    handlers = {
        "signin": cmd_signin,
        "signout": cmd_signout,
        "doctor": cmd_doctor,
        "capture": cmd_capture,
        "crop": cmd_crop,
        "selftest": cmd_selftest,
        "tour": cmd_tour,
    }
    try:
        return handlers[args.cmd](cfg, args)
    except SafetyAbort as e:
        log.error("SAFETY ABORT (no tap): %s", e)
        return 4
    except (FlowError, AdbError) as e:
        log.error("flow/adb failure: %s", e)
        return 2 if isinstance(e, FlowError) else 3
    except EnvError as e:
        log.error("environment failure: %s", e)
        return 3
    except Exception as e:
        log.exception("unexpected failure: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())