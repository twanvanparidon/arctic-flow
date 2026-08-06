#!/usr/bin/env python3
"""Entry point for the workflow engine.

    python3 src/main.py --help

Deliberately almost empty. It puts `src/` on the import path and hands over to `cli/`,
which owns arguments, help and output. Keeping those here would mix the interface with
the bootstrap and give the CLI nowhere to grow.
"""

from __future__ import annotations

import sys
from pathlib import Path

# src/ on the path, so `cli`, `engine`, `paths` and the rest import by their own names.
# The same names they have once installed, so an import that works here works there.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
