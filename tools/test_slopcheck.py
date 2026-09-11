#!/usr/bin/env python3
"""Tests for tools/slopcheck.py, wireable into CI.

The substance is slopcheck's own --selftest, which proves every rule fires on a
known-bad fixture and stays quiet on a known-good one. This wrapper runs it as
a test and adds the invariants a fixture cannot express: that the rule table
stays documented, that the escape hatches keep demanding a reason, and that the
tool's exit codes mean what the docstring says they mean.

    python3 -m unittest discover -s tools -p "test_*.py"
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import slopcheck as sc  # noqa: E402

SLOP = ("In today's fast-paced world, this seamless dashboard delves into a "
        "rich tapestry of value. It's not just a tool, it's a paradigm shift.\n")
CLEAN = ("The tracker lists 49 filings from 2014 onward. Each row carries its "
         "docket number and the date the order issued.\n")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "slopcheck.py"), *args],
                          capture_output=True, text=True)


class SelfTest(unittest.TestCase):
    def test_every_rule_still_fires(self):
        """The fixture suite. If this fails, a rule has gone silent."""
        proc = run("--selftest")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("all rules fire", proc.stdout)

    def test_selftest_can_fail(self):
        """A selftest that cannot go red proves nothing.

        Sabotage a rule in a copy of the module and confirm the harness
        notices. Without this, a --selftest hard-coded to print success would
        pass this file forever.
        """
        src = (ROOT / "slopcheck.py").read_text()
        broken = src.replace(
            r'r"\b([A-Za-z][a-z]{2,14}),\s+([A-Za-z][a-z]{2,14}),\s+and\s+([A-Za-z][a-z]{2,14})\b")',
            r'r"\b([A-Za-z][a-z]{2,14}),\s+([A-Za-z][a-z]{2,14}),\s+and\s+([A-Za-z][a-z]{99})\b")')
        self.assertNotEqual(src, broken, "the sabotage target moved; update this test")
        with tempfile.TemporaryDirectory() as d:
            mutant = Path(d) / "mutant.py"
            mutant.write_text(broken)
            proc = subprocess.run([sys.executable, str(mutant), "--selftest"],
                                  capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, "sabotaging tricolon did not turn the selftest red")
        self.assertIn("tricolon", proc.stdout)


class RuleTable(unittest.TestCase):
    def test_ids_are_unique(self):
        self.assertEqual(len(sc.RULES_BY_ID), len(sc.RULES))

    def test_every_rule_is_documented(self):
        for rule in sc.RULES:
            self.assertTrue(rule.why.strip(), rule.id)
            self.assertTrue(rule.fix.strip(), rule.id)
            self.assertIn(rule.level, sc.LEVELS, rule.id)
            self.assertTrue(rule.scopes, rule.id)

    def test_every_rule_names_where_it_came_from(self):
        """A rule with no provenance is a rule nobody can argue with later."""
        missing = [r.id for r in sc.RULES if not r.origin.strip()]
        self.assertEqual(missing, [], "rules with no origin: {}".format(missing))

    def test_gating_rules_carry_fixtures(self):
        """FAIL and WARN gate a merge, so both must be provable."""
        missing = [r.id for r in sc.RULES
                   if r.level in ("FAIL", "WARN") and not r.bad and r.family != "corpus"]
        self.assertEqual(missing, [], "gating rules with no known-bad fixture: {}".format(missing))

    def test_every_threshold_is_named_in_defaults(self):
        """A threshold read but never declared would silently KeyError in CI."""
        text = (ROOT / "slopcheck.py").read_text()
        import re
        used = set(re.findall(r'threshold\("([a-z_0-9]+)"\)', text))
        self.assertTrue(used)
        self.assertEqual(sorted(used - set(sc.DEFAULTS)), [])


class EscapeHatches(unittest.TestCase):
    def test_a_reason_must_be_a_reason(self):
        for junk in ("", "TODO", "n/a", "later", "because"):
            with self.assertRaises(SystemExit, msg=junk):
                sc._check_reason("test", junk)

    def test_a_real_reason_passes(self):
        self.assertTrue(sc._check_reason(
            "test", "CPUC term of art in P.U. Code 216.6(a); rewriting misquotes the rule"))

    def test_unknown_rule_id_in_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".slopcheck.json").write_text(
                '{"disable": {"no-such-rule": "a reason long enough to pass the check"}}')
            with self.assertRaises(SystemExit):
                sc.load_config(Path(d))

    def test_disabling_a_rule_needs_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".slopcheck.json").write_text('{"disable": {"tricolon": "TODO"}}')
            with self.assertRaises(SystemExit):
                sc.load_config(Path(d))


class ExitCodes(unittest.TestCase):
    def test_slop_fails_and_clean_passes(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "slop.md").write_text(SLOP)
            self.assertEqual(run(str(Path(d) / "slop.md")).returncode, 1)
            (Path(d) / "clean.md").write_text(CLEAN)
            self.assertEqual(run(str(Path(d) / "clean.md")).returncode, 0)

    def test_scanning_nothing_is_an_error_not_a_pass(self):
        """The whole point: an empty input set must never report success."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "data.bin").write_bytes(b"\x00\x01")
            proc = run(d)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_the_run_always_says_how_much_it_examined(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "clean.md").write_text(CLEAN)
            out = run(d).stdout
            for token in ("chunks", "paragraphs", "sentences", "words", "rules active"):
                self.assertIn(token, out)

    def test_contributor_docs_are_skipped_and_said_so(self):
        """Skipping is fine; skipping quietly is not."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "CLAUDE.md").write_text(SLOP)
            (Path(d) / "page.md").write_text(CLEAN)
            out = run(d).stdout
            self.assertIn("skipped 1 contributor doc", out)
            self.assertEqual(run(d).returncode, 0)
            self.assertEqual(run("--include-agent-docs", d).returncode, 1)


if __name__ == "__main__":
    unittest.main()
