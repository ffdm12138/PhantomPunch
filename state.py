import csv
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class DayState:
    date: str
    decision: bool = False
    decision_reason: str = ""
    signin_done: bool = False
    signin_at: str = None
    signin_source: str = None
    signout_done: bool = False
    signout_at: str = None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load(path: Path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return DayState(**{f: raw.get(f, getattr(DayState(date=""), f)) for f in DayState.__dataclass_fields__})
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, OSError) as e:
        log.warning("state corrupt, treating as missing: %s", e)
        return None


def save_atomic(path: Path, st: DayState):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


LEDGER_HEADER = ["日期", "类型", "打卡时间", "来源", "结果", "证据"]


def append_record(path, row: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(LEDGER_HEADER)
        w.writerow([row.get(k, "") for k in LEDGER_HEADER])


@contextmanager
def acquire_lock(path: Path, stale_s: int = 600):
    path = Path(path)
    owned = False
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode())
            os.close(fd)
            owned = True
            break
        except FileExistsError:
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
                age = time.time() - float(info.get("ts", 0))
            except (ValueError, OSError):
                age = stale_s + 1
            if age > stale_s and attempt == 0:
                path.unlink(missing_ok=True)
                continue
            yield None
            return
    try:
        yield owned
    finally:
        if owned:
            path.unlink(missing_ok=True)