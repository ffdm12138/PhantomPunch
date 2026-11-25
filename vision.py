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
    x1, y1, x2, y2 = (max(0, min(d, int(v * d))) for v, d in zip(roi, (w, h, w, h)))
    return g[y1:y2, x1:x2], x1, y1


def _match_one_gray(g, ox, oy, tpl, threshold):
    t = _gray(tpl)
    if min(t.shape[:2]) < 1 or t.shape[0] > g.shape[0] or t.shape[1] > g.shape[1]:
        return None
    if float(t.std()) < 1e-6:
        return None
    res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    if mx >= threshold:
        return float(mx), loc[0] + ox + t.shape[1] // 2, loc[1] + oy + t.shape[0] // 2
    return None


def _match_all_gray(g, ox, oy, tpl, threshold):
    t = _gray(tpl)
    if min(t.shape[:2]) < 1 or t.shape[0] > g.shape[0] or t.shape[1] > g.shape[1]:
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


def match_one(img, tpl, threshold, roi=None):
    g, ox, oy = _roi_crop(_gray(img), roi)
    return _match_one_gray(g, ox, oy, tpl, threshold)


def match_all(img, tpl, threshold, roi=None):
    g, ox, oy = _roi_crop(_gray(img), roi)
    return _match_all_gray(g, ox, oy, tpl, threshold)


def topmost(hits):
    return min(hits, key=lambda h: h[2]) if hits else None


def save_png(img, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), img):
        raise OSError(f"imwrite failed: {path}")


def find_blue_button(img):
    """Locate the big blue punch circle; layout/render independent. -> (score, cx, cy, r) | None"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (100, 150, 120), (125, 255, 255))
    k = np.ones((9, 9), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = img.shape[:2]
    best = None
    for c in contours:
        area = cv2.contourArea(c)
        if area < 0.008 * w * h:
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        circ = 4 * np.pi * area / (peri * peri)
        (cx, cy), r = cv2.minEnclosingCircle(c)
        if r <= 0:
            continue
        fill = area / (np.pi * r * r)
        d = 2 * r
        if circ < 0.75 or fill < 0.6:
            continue
        if not (0.2 * w <= d <= 0.6 * w):
            continue
        if not (0.25 * w <= cx <= 0.75 * w and 0.30 * h <= cy <= 0.78 * h):
            continue
        score = circ * fill
        if best is None or score > best[0]:
            best = (float(score), int(cx), int(cy), int(r))
    return best


def match_one_scales(img, tpl, threshold, roi=None, scales=(1.0, 0.95, 1.05, 0.9, 1.1)):
    g, ox, oy = _roi_crop(_gray(img), roi)
    tgray = _gray(tpl)
    best = None
    for s in scales:
        t = tgray if s == 1.0 else cv2.resize(tgray, None, fx=s, fy=s)
        hit = _match_one_gray(g, ox, oy, t, threshold)
        if hit is not None and (best is None or hit[0] > best[0]):
            best = hit
    return best


def match_all_scales(img, tpl, threshold, roi=None, scales=(1.0, 0.95, 1.05, 0.9, 1.1)):
    g, ox, oy = _roi_crop(_gray(img), roi)
    tgray = _gray(tpl)
    merged = []
    for s in scales:
        t = tgray if s == 1.0 else cv2.resize(tgray, None, fx=s, fy=s)
        for score, cx, cy in _match_all_gray(g, ox, oy, t, threshold):
            merged.append((score, cx, cy, t.shape[1], t.shape[0]))
    merged.sort(key=lambda h: -h[0])
    kept = []
    for score, cx, cy, tw, th in merged:
        if all(abs(cx - k[1]) >= k[3] * 0.5 or abs(cy - k[2]) >= k[4] * 0.5 for k in kept):
            kept.append((score, cx, cy, tw, th))
    return [(s, x, y) for s, x, y, _, _ in kept]