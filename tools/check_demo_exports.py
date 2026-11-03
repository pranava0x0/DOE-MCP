"""Replay the public CLI and compare its exported bytes with the downloads."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from doe_mcp.workflows import CASES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", help="default: every recorded walkthrough")
    cases = parser.parse_args().cases or list(CASES)
    public = Path(__file__).resolve().parents[1] / "docs/data/demos"
    with TemporaryDirectory() as tmp:
        for case in cases:
            if case not in CASES:
                parser.error(f"unknown walkthrough: {case}")
            output = Path(tmp) / case
            subprocess.run([sys.executable, "-m", "doe_mcp.cli", "demo", case,
                            "--output", str(output)], check=True, stdout=subprocess.DEVNULL)
            for item in output.iterdir():
                served = public / item.name
                if not served.is_file() or item.read_bytes() != served.read_bytes():
                    sys.exit(f"download differs from CLI: {item.name}")
        if set(cases) == set(CASES):
            built = {p.name for p in Path(tmp).glob("*/*")}
            stale = sorted(p.name for p in public.iterdir() if p.name not in built)
            if stale:
                sys.exit(f"downloads without a CLI export: {', '.join(stale)}")
    print(f"CLI and site downloads match for {len(cases)} walkthroughs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
