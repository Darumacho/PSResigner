"""PyInstaller entry point for the CLI (same relative-import reason as run_gui.py)."""

import sys

from ps3save.cli import main

if __name__ == "__main__":
    sys.exit(main())
