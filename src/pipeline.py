"""
pipeline.py - one command to rebuild every output from the raw CSVs, in order.

    python -m src.pipeline                     # verify -> audit -> clean
    python -m src.pipeline --steps clean       # run selected steps only

Steps stop the pipeline on the first failure. Later days add steps here
(kpis, funnel, cohorts, rfm, model, exports) so the whole project stays
reproducible with one command.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Callable

from src import audit_raw, clean, manifest


def step_verify() -> bool:
    """Raw files must match docs/data_manifest.json (size, rows, SHA-256)."""
    return manifest.verify(manifest.config.RAW_DIR) == 0


def step_audit() -> bool:
    audit_raw.main([])
    return True


def step_clean() -> bool:
    try:
        clean.main([])
    except SystemExit as e:  # clean.main exits non-zero on validation failure
        return e.code in (None, 0)
    return True


STEPS: dict[str, Callable[[], bool]] = {"verify": step_verify, "audit": step_audit, "clean": step_clean}


def run(steps: list[str]) -> int:
    for name in steps:
        t = time.perf_counter()
        print(f"\n=== pipeline step: {name} ===", file=sys.stderr, flush=True)
        if not STEPS[name]():
            print(f"=== step '{name}' FAILED - pipeline stopped ===", file=sys.stderr)
            return 1
        print(f"=== step '{name}' done in {time.perf_counter() - t:,.1f}s ===", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Run the project pipeline.")
    p.add_argument("--steps", default=",".join(STEPS),
                   help=f"comma-separated subset of: {', '.join(STEPS)} (default: all, in order)")
    a = p.parse_args(argv)
    steps = [s.strip() for s in a.steps.split(",") if s.strip()]
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        sys.exit(f"Unknown step(s): {unknown}. Choose from {list(STEPS)}.")
    order = list(STEPS)
    sys.exit(run(sorted(steps, key=order.index)))


if __name__ == "__main__":
    main()
