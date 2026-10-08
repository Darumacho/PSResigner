"""PyInstaller entry point for the GUI (ps3save/gui.py uses relative
imports, so it needs to be launched as a package, not as a bare script)."""

from ps3save.gui import main

if __name__ == "__main__":
    main()
