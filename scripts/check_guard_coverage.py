"""Fail the build if the capability boundary is not fully covered.

Everywhere else a missing branch is a gap in the tests. In llm.py it is a path
by which the agent might reach a lock, so it is treated differently.
"""

import json
import sys
from pathlib import Path

GUARDED = (
    "custom_components/hermes_conversation/llm.py",
    "custom_components/hermes_conversation/policy.py",
)
REQUIRED_PERCENT = 100


def main() -> int:
    """Return a non-zero exit code when any guarded file is under-covered."""
    report = json.loads(Path("coverage.json").read_text())
    files = report["files"]
    return max(_check(files, path) for path in GUARDED)


def _check(files: dict, path: str) -> int:
    if path not in files:
        print(f"{path} was not measured", file=sys.stderr)
        return 1

    summary = files[path]["summary"]
    percent = summary["percent_covered"]
    if percent < REQUIRED_PERCENT:
        missing = files[path]["missing_lines"]
        branches = files[path].get("missing_branches", [])
        print(
            f"{path} is {percent:.1f}% covered, needs {REQUIRED_PERCENT}%. "
            f"Uncovered lines: {missing}; uncovered branches: {branches}",
            file=sys.stderr,
        )
        return 1

    print(f"{path}: {percent:.0f}% covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
