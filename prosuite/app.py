"""Application entry point."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from . import logging_setup
from .ui.main_window import MainWindow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ProSuite", description="ProSuite")
    parser.add_argument(
        "--no-hotkeys",
        action="store_true",
        help=(
            "Start without installing global keyboard/mouse hooks. Keybinds "
            "will not work, but nothing is registered in the system-wide input "
            "path - useful when a third-party HID driver is unstable."
        ),
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable verbose logging."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import logging

    args = parse_args(argv)
    logging_setup.configure(logging.DEBUG if args.debug else logging.INFO)

    app = QApplication(sys.argv[:1])
    window = MainWindow(enable_hotkeys=not args.no_hotkeys)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
