import copy
import json
import os
import unittest

from fmstatus import rules

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
GEN = 1790149935  # generated_epoch of every fixture except stale.json
NOW = GEN + 30


def load(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def ev(summary, now=NOW, memory=None, **kw):
    return rules.evaluate(summary, now, memory, **kw)


class RuleBranches(unittest.TestCase):
    def test_active_is_yellow_with_first_child(self):
        status, _ = ev(load("active"))
        self.assertEqual(status["level"], "yellow")
        self.assertEqual(status["label"], "2 working - Evosus Helper: polish UI...")
        self.assertEqual(status["n"], {"calls": 0, "working": 2, "held": 1})
        self.assertEqual(status["age"], 30)

    def test_captain_decision_is_red_immediately(self):
        status, _ = ev(load("captain_decision"))
        self.assertEqual(status["level"], "red")
        self.assertEqual(status["label"], "1 call - RAIS: link applicable models...")
        self.assertEqual(status["n"]["calls"], 1)

    def test_captain_decision_without_listed_decision_is_still_red(self):
        summary = load("captain_decision")
        summary["decisions_open"] = []  # truncated by the producer's cap
        status, _ = ev(summary)
        self.assertEqual((status["level"], status["label"]), ("red", "1 call"))

    def test_held_is_green_with_note(self):
        status, _ = ev(load("held"))
        self.assertEqual((status["level"], status["label"]), ("green", "ready - 1 held"))

    def test_idle_is_green(self):
        status, _ = ev(load("idle"))
        self.assertEqual((status["level"], status["label"]), ("green", "ready"))

    def test_unknown_state_is_stale(self):
        status, memory = ev(load("unknown"))
        self.assertEqual((status["level"], status["label"]), ("stale", "fm unknown"))
        self.assertEqual(memory, {})

    def test_unrecognised_state_is_stale(self):
        summary = load("idle")
        summary["state"] = "something_new"
        self.assertEqual(ev(summary)[0]["level"], "stale")


class Staleness(unittest.TestCase):
    def test_missing_summary(self):
        status, _ = ev(None)
        self.assertEqual((status["level"], status["label"]), ("stale", "fm summary missing"))

    def test_bad_schema(self):
        summary = load("idle")
        summary["schema"] = "fm-secondmate-home-summary.v2"
        status, _ = ev(summary)
        self.assertEqual((status["level"], status["label"]), ("stale", "fm schema mismatch"))

    def test_missing_generated_epoch(self):
        summary = load("idle")
        del summary["generated_epoch"]
        self.assertEqual(ev(summary)[0]["label"], "fm summary bad")

    def test_old_summary_is_stale(self):
        status, _ = ev(load("stale"))
        self.assertEqual(status["level"], "stale")
        self.assertEqual(status["label"], "fm stale 172m")

    def test_age_boundary(self):
        summary = load("idle")
        self.assertEqual(ev(summary, now=GEN + 900)[0]["level"], "green")
        self.assertEqual(ev(summary, now=GEN + 901)[0]["level"], "stale")

    def test_future_summary_has_zero_age(self):
        self.assertEqual(ev(load("idle"), now=GEN - 5)[0]["age"], 0)


class Debounce(unittest.TestCase):
    def test_blocked_stays_yellow_until_debounce_elapses(self):
        summary = load("blocked")
        status, memory = ev(summary, now=NOW)
        self.assertEqual(status["level"], "yellow")
        status, memory = ev(summary, now=NOW + 59, memory=memory)
        self.assertEqual(status["level"], "yellow")
        status, memory = ev(summary, now=NOW + 60, memory=memory)
        self.assertEqual(status["level"], "red")
        self.assertEqual(status["label"], "1 call - CI red on lint job - cannot...")

    def test_resolved_decision_resets_debounce(self):
        summary = load("blocked")
        _, memory = ev(summary, now=NOW)
        _, memory = ev(load("active"), now=NOW + 30, memory=memory)
        self.assertEqual(memory, {})
        status, _ = ev(summary, now=NOW + 70, memory=memory)
        self.assertEqual(status["level"], "yellow")

    def test_failed_endpoint_is_debounced_red(self):
        summary = load("idle")
        summary["endpoints"] = [{"id": "partsmap-x", "state": "failed", "source": "status-log"}]
        status, memory = ev(summary, now=NOW)
        self.assertEqual(status["level"], "green")
        status, _ = ev(summary, now=NOW + 60, memory=memory)
        self.assertEqual((status["level"], status["label"]), ("red", "1 call - failed: partsmap-x"))

    def test_custom_debounce(self):
        status, _ = ev(load("blocked"), debounce_s=0)
        self.assertEqual(status["level"], "red")

    def test_non_red_verbs_are_ignored(self):
        summary = load("blocked")
        summary["decisions_open"][0]["verb"] = "note"
        self.assertEqual(ev(summary, debounce_s=0)[0]["level"], "yellow")


class Labels(unittest.TestCase):
    def test_sanitize_ascii(self):
        self.assertEqual(rules.sanitize_label("a · b — c… café \U0001F680"),
                         "a - b - c... cafe")

    def test_sanitize_strips_control_and_collapses_space(self):
        self.assertEqual(rules.sanitize_label("x\x03\x04\n\t  y"), "x y")

    def test_truncates_to_limit(self):
        label = rules.sanitize_label("x" * 100)
        self.assertEqual(len(label), rules.LABEL_MAX)
        self.assertTrue(label.endswith("..."))

    def test_every_label_is_ascii_and_bounded(self):
        for name in ("active", "captain_decision", "blocked", "held", "idle", "unknown", "stale"):
            status, _ = ev(load(name), debounce_s=0)
            self.assertIn(status["level"], rules.LEVELS)
            status["label"].encode("ascii")
            self.assertLessEqual(len(status["label"]), rules.LABEL_MAX)

    def test_counts_mode_hides_titles(self):
        self.assertEqual(ev(load("active"), labels="counts")[0]["label"], "2 working")
        self.assertEqual(ev(load("captain_decision"), labels="counts")[0]["label"], "1 call")

    def test_does_not_mutate_input(self):
        summary = load("blocked")
        before = copy.deepcopy(summary)
        ev(summary, debounce_s=0)
        self.assertEqual(summary, before)


if __name__ == "__main__":
    unittest.main()
