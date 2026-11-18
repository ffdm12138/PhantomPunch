import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Spec:
    name: str
    path: Path
    threshold: float
    roi: tuple = None
    source: str = None


def load_manifest(tdir: Path) -> dict:
    tdir = Path(tdir)
    mf = tdir / "manifest.json"
    if not mf.is_file():
        return {}
    raw = json.loads(mf.read_text(encoding="utf-8"))
    specs = {}
    for t in raw.get("templates", []):
        if t.get("status") == "N/A":
            continue
        specs[t["name"]] = Spec(
            name=t["name"],
            path=tdir / t["file"],
            threshold=float(t.get("threshold", 0.85)),
            roi=tuple(t["roi"]) if t.get("roi") else None,
            source=t.get("source"),
        )
    return specs


def load_template(spec: Spec) -> np.ndarray:
    img = cv2.imread(str(spec.path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"template unreadable: {spec.path}")
    return img


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _roi_crop(g, roi):
    if not roi:
        return g, 0, 0
    h, w = g.shape[:2]
    x1, y1, x2, y2 = (int(v * d) for v, d in zip(roi, (w, h, w, h)))
    return g[y1:y2, x1:x2], x1, y1


def match_one(img, tpl, threshold, roi=None):
    g, ox, oy = _roi_crop(_gray(img), roi)
    t = _gray(tpl)
    if t.shape[0] > g.shape[0] or t.shape[1] > g.shape[1]:
        return None
    if float(t.std()) < 1e-6:
        return None
    res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    if mx >= threshold:
        return float(mx), loc[0] + ox + t.shape[1] // 2, loc[1] + oy + t.shape[0] // 2
    return None


def match_all(img, tpl, threshold, roi=None):
    g, ox, oy = _roi_crop(_gray(img), roi)
    t = _gray(tpl)
    if t.shape[0] > g.shape[0] or t.shape[1] > g.shape[1]:
        return []
    if float(t.std()) < 1e-6:
        return []
    res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    hits = sorted(
        ((float(res[y, x]), int(x) + ox + t.shape[1] // 2, int(y) + oy + t.shape[0] // 2) for y, x in zip(ys, xs)),
        key=lambda h: -h[0],
    )
    kept = []
    for h in hits:
        if all(abs(h[1] - k[1]) >= t.shape[1] / 2 or abs(h[2] - k[2]) >= t.shape[0] / 2 for k in kept):
            kept.append(h)
    return kept


def topmost(hits):
    return min(hits, key=lambda h: h[2]) if hits else None


def save_png(img, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), img):
        raise OSError(f"imwrite failed: {path}")