#!/usr/bin/env python3
"""Watch /app/src and restart the wrapped process on Python file changes (dev only)."""

from __future__ import annotations

import shlex
import sys

from watchfiles import run_process


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: watch_src.py <command> [args...]", file=sys.stderr)
        sys.exit(2)
    shell_cmd = " ".join(shlex.quote(arg) for arg in sys.argv[1:])
    run_process("/app/src", target=shell_cmd, args=())


if __name__ == "__main__":
    main()
