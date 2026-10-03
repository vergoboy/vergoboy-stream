#!/usr/bin/env python3
"""Regenerate the golden argv fixtures under tests/fixtures/argv/.

A golden test is only worth having if the golden values came from the code
rather than from a human's reading of it. These files are produced by actually
calling the builders and dumping the result one argument per line.

The scenario table lives in ``tests/argv_cases.py`` so this script and the test
that checks the goldens can never drift apart.

Review the diff before committing a regeneration — a golden file that changes
silently is exactly how a regression gets waved through.

    ./venv/bin/python scripts/capture_argv_fixtures.py           # write
    ./venv/bin/python scripts/capture_argv_fixtures.py --check   # verify only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.argv_cases import CASES, render  # noqa: E402

OUT_DIR = ROOT / "tests" / "fixtures" / "argv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fail if any golden differs, without writing")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    changed, same, failed = [], [], []

    for name, build in sorted(CASES.items()):
        dest = OUT_DIR / f"{name}.txt"
        try:
            body = render(build())
        except Exception as exc:  # noqa: BLE001
            # Report and carry on: one broken case must not hide the state of
            # every other golden in the set.
            failed.append(name)
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
            continue
        if dest.exists() and dest.read_text() == body:
            same.append(name)
            continue
        changed.append(name)
        if args.check:
            print(f"CHANGED {name}")
        else:
            dest.write_text(body)
            print(f"wrote {name}")

    print(f"\n{len(changed)} changed, {len(same)} unchanged ({OUT_DIR.relative_to(ROOT)})")
    if failed:
        print(f"ERRORED: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
