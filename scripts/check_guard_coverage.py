"""Fail the build if the capability boundary is not fully covered.

Everywhere else a missing branch is a gap in the tests. In llm.py it is a path
by which the agent might reach a lock, so it is treated differently.
"""

import json
import sys
from pathlib import Path

GUARDED = "custom_components/hermes_conversation/llm.py"
REQUIRED_PERCENT = 100


def main() -> int:
    """Return a non-zero exit code when the guard is under-covered."""
    report = json.loads(Path("coverage.json").read_text())
    files = report["files"]

    if GUARDED not in files:
        print(f"{GUARDED} was not measured", file=sys.stderr)
        return 1

    summary = files[GUARDED]["summary"]
    percent = summary["percent_covered"]
    if percent < REQUIRED_PERCENT:
        missing = files[GUARDED]["missing_lines"]
        print(
            f"{GUARDED} is {percent:.1f}% covered, needs {REQUIRED_PERCENT}%. "
            f"Uncovered lines: {missing}",
            file=sys.stderr,
        )
        return 1

    print(f"{GUARDED}: {percent:.0f}% covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
