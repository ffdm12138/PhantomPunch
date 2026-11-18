import random
from datetime import date


def is_excluded(d: date, ranges: dict) -> bool:
    for a, b in ranges.get(d.year, []):
        if a <= d <= b:
            return True
    return False


def roll(num: int, den: int, rng=None) -> bool:
    r = rng if rng is not None else random
    return r.random() < num / den


def signin_plan(state, today: date, excluded: bool, force: bool, num: int = 5, den: int = 7, rng=None):
    """-> (action, decision, reason); action in {"run","skip"}."""
    existing = state if state is not None and state.date == today.isoformat() else None
    if existing is not None:
        if existing.signin_done:
            return "skip", existing.decision, "already_done"
        if not force:
            if existing.decision:
                return "run", True, "recover"
            return "skip", False, "decision_no"
    if force:
        return "run", True, "forced"
    if excluded:
        return "skip", False, "excluded"
    d = roll(num, den, rng)
    return ("run" if d else "skip"), d, "rolled"


def signout_gate(state, today: date, force: bool):
    if force:
        return "run", "forced"
    if state is None or state.date != today.isoformat():
        return "skip", "no_state"
    if not state.decision:
        return "skip", "decision_no"
    if not state.signin_done:
        return "skip", "signin_not_done"
    if state.signout_done:
        return "skip", "already_done"
    return "run", "ok"