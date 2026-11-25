from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    pass


@dataclass
class Config:
    adb_path: Path
    serial: str
    package: str
    probability_num: int
    probability_den: int
    exclude_ranges: dict
    match_threshold: float
    deadline_s: int
    device_wait_s: int
    kill_adb_after: bool
    log_retention_days: int
    tap_offset_ratio: float = 0.25
    range_wait_s: float = 45.0
    settle_s: float = 1.5
    templates_dir: Path = field(default_factory=lambda: ROOT / "assets" / "templates")
    captures_dir: Path = field(default_factory=lambda: ROOT / "assets" / "captures")
    logs_dir: Path = field(default_factory=lambda: ROOT / "logs")
    state_path: Path = field(default_factory=lambda: ROOT / "state.json")
    lock_path: Path = field(default_factory=lambda: ROOT / "state.lock")
    records_path: Path = field(default_factory=lambda: ROOT / "records" / "punches.csv")


def load_config(path=None) -> Config:
    p = Path(path) if path else ROOT / "config.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"config unreadable: {p}: {e}") from e
    try:
        cfg = Config(
            adb_path=Path(raw["adb_path"]),
            serial=str(raw["serial"]),
            package=str(raw["package"]),
            probability_num=int(raw["probability_num"]),
            probability_den=int(raw["probability_den"]),
            exclude_ranges={
                int(y): [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in rs]
                for y, rs in raw["exclude_ranges"].items()
            },
            match_threshold=float(raw["match_threshold"]),
            deadline_s=int(raw["deadline_s"]),
            device_wait_s=int(raw["device_wait_s"]),
            kill_adb_after=bool(raw["kill_adb_after"]),
            log_retention_days=int(raw["log_retention_days"]),
            tap_offset_ratio=float(raw.get("tap_offset_ratio", 0.25)),
            range_wait_s=float(raw.get("range_wait_s", 45.0)),
            settle_s=float(raw.get("settle_s", 1.5)),
        )
    except (KeyError, ValueError, TypeError) as e:
        raise ConfigError(f"config invalid: {e}") from e
    if not cfg.adb_path.is_file():
        raise ConfigError(f"adb not found: {cfg.adb_path}")
    if not cfg.serial:
        raise ConfigError("serial empty")
    if not (0 <= cfg.probability_num <= cfg.probability_den) or cfg.probability_den <= 0:
        raise ConfigError("probability out of range")
    if not (0 < cfg.match_threshold <= 1.0):
        raise ConfigError("match_threshold out of range 0..1")
    if cfg.deadline_s <= 0 or cfg.device_wait_s < 0 or cfg.log_retention_days < 0:
        raise ConfigError("timeout/retention must be positive/non-negative")
    if not (0 <= cfg.tap_offset_ratio <= 0.5):
        raise ConfigError("tap_offset_ratio out of range 0..0.5")
    if not (0 <= cfg.range_wait_s <= 180):
        raise ConfigError("range_wait_s out of range 0..180")
    if not (0 <= cfg.settle_s <= 10):
        raise ConfigError("settle_s out of range 0..10")
    for ranges in cfg.exclude_ranges.values():
        for a, b in ranges:
            if a > b:
                raise ConfigError(f"exclude range inverted: {a}..{b}")
    return cfg