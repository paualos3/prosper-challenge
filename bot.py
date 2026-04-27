"""Compatibility entry point for running the Prosper bot with `uv run bot.py`."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

bot = import_module("src.bot").bot


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
