import random
import unittest
from datetime import date

import scheduling as S
from state import DayState

RANGES = {
    2027: [(date(2027, 2, 5), date(2027, 2, 13)), (date(2027, 10, 1), date(2027, 10, 7))]
}
TODAY = date(2026, 11, 18)


class TestExcluded(unittest.TestCase):
    def test_closed_bounds(self):
        for d in (date(2027, 2, 5), date(2027, 2, 13), date(2027, 10, 1), date(2027, 10, 7)):
            self.assertTrue(S.is_excluded(d, RANGES), d)

    def test_makeup_workdays_not_excluded(self):
        for d in (date(2027, 1, 31), date(2027, 2, 14), date(2027, 9, 26), date(2027, 10, 9)):
            self.assertFalse(S.is_excluded(d, RANGES), d)

    def test_weekends_and_other_holidays_trigger(self):
        for d in (date(2026, 11, 21), date(2026, 11, 22), date(2027, 5, 1), date(2027, 4, 5), date(2027, 1, 1)):
            self.assertFalse(S.is_excluded(d, RANGES), d)

    def test_missing_year_not_excluded(self):
        self.assertFalse(S.is_excluded(date(2028, 2, 10), RANGES))


class TestRoll(unittest.TestCase):
    def test_distribution(self):
        rng = random.Random(42)
        n = 200000
        hits = sum(S.roll(5, 7, rng) for _ in range(n))
        self.assertAlmostEqual(hits / n, 5 / 7, delta=0.01)

    def test_extremes(self):
        self.assertTrue(S.roll(7, 7, random.Random(1)))
        self.assertFalse(S.roll(0, 7, random.Random(1)))


class TestSigninPlan(unittest.TestCase):
    def test_new_day_rolled(self):
        rng = random.Random(7)
        action, decision, reason = S.signin_plan(None, TODAY, False, False, rng=rng)
        self.assertEqual(reason, "rolled")
        self.assertEqual(action, "run" if decision else "skip")

    def test_excluded(self):
        self.assertEqual(S.signin_plan(None, date(2027, 2, 6), True, False), ("skip", False, "excluded"))

    def test_force_new(self):
        self.assertEqual(S.signin_plan(None, date(2027, 2, 6), True, True), ("run", True, "forced"))

    def test_already_done(self):
        st = DayState(date=TODAY.isoformat(), decision=True, signin_done=True)
        self.assertEqual(S.signin_plan(st, TODAY, False, False)[2], "already_done")
        self.assertEqual(S.signin_plan(st, TODAY, False, False)[0], "skip")

    def test_recover(self):
        st = DayState(date=TODAY.isoformat(), decision=True, signin_done=False)
        self.assertEqual(S.signin_plan(st, TODAY, False, False), ("run", True, "recover"))

    def test_decision_no(self):
        st = DayState(date=TODAY.isoformat(), decision=False)
        self.assertEqual(S.signin_plan(st, TODAY, False, False), ("skip", False, "decision_no"))

    def test_force_overrides_decision_no(self):
        st = DayState(date=TODAY.isoformat(), decision=False)
        self.assertEqual(S.signin_plan(st, TODAY, False, True), ("run", True, "forced"))

    def test_stale_state_ignored(self):
        st = DayState(date="2026-11-17", decision=True, signin_done=True)
        action, _, reason = S.signin_plan(st, TODAY, False, False, rng=random.Random(3))
        self.assertEqual(reason, "rolled")


class TestSignoutGate(unittest.TestCase):
    def test_no_state(self):
        self.assertEqual(S.signout_gate(None, TODAY, False), ("skip", "no_state"))

    def test_stale_date(self):
        st = DayState(date="2026-11-17", decision=True, signin_done=True)
        self.assertEqual(S.signout_gate(st, TODAY, False), ("skip", "no_state"))

    def test_decision_no(self):
        st = DayState(date=TODAY.isoformat(), decision=False)
        self.assertEqual(S.signout_gate(st, TODAY, False), ("skip", "decision_no"))

    def test_signin_not_done(self):
        st = DayState(date=TODAY.isoformat(), decision=True, signin_done=False)
        self.assertEqual(S.signout_gate(st, TODAY, False), ("skip", "signin_not_done"))

    def test_already_done(self):
        st = DayState(date=TODAY.isoformat(), decision=True, signin_done=True, signout_done=True)
        self.assertEqual(S.signout_gate(st, TODAY, False), ("skip", "already_done"))

    def test_ok(self):
        st = DayState(date=TODAY.isoformat(), decision=True, signin_done=True)
        self.assertEqual(S.signout_gate(st, TODAY, False), ("run", "ok"))

    def test_force(self):
        self.assertEqual(S.signout_gate(None, TODAY, True), ("run", "forced"))


if __name__ == "__main__":
    unittest.main()