#!/usr/bin/env python3
"""
main.py
-------
Entry point for the SCM Device Viewer.

    python main.py                  open the GUI empty
    python main.py file.scm         open the GUI with a file loaded
    python main.py --check file.scm parse and validate headlessly, no GUI
                                    (useful in scripts; needs no display)
"""

import os
import sys

# make the package importable no matter where it is launched from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_headless(path: str) -> int:
    """Parse + validate with no Qt and no VTK.  Returns a shell exit code."""
    from parser.scm_parser import ScmParser
    from validation.geometry_checks import run_all_checks, FAIL

    result = ScmParser().parse_file(path)
    print(f"=== {os.path.basename(path)} ===")
    for m in result.messages:
        print(" ", m)
    print(f"\n{result.summary()}\n")

    print("=== geometry checks ===")
    failed = 0
    for c in run_all_checks(result.regions, result.contacts):
        print(f"  {c.status:5s} {c.name}: {c.detail}")
        for item in (c.items or [])[:10]:
            print(f"           - {item}")
        if c.status == FAIL:
            failed += 1
    return 1 if (failed or result.errors) else 0


def run_gui(path=None) -> int:
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SCM Device Viewer")
    win = MainWindow()
    win.show()
    if path:
        win.load_path(path)
    return app.exec()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("--check", "-c"):
        if len(args) < 2:
            print("usage: python main.py --check FILE.scm")
            sys.exit(2)
        sys.exit(run_headless(args[1]))
    sys.exit(run_gui(args[0] if args else None))