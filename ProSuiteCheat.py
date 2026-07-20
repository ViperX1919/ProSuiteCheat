"""ProSuite launcher.

The implementation lives in the ``prosuite`` package; this file only exists so
``python ProSuiteCheat.py`` and the PyInstaller spec keep working.
"""

from prosuite.app import main

if __name__ == "__main__":
    raise SystemExit(main())
