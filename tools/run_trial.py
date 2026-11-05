"""Compatibility entry point; use the same startup and diagnostics as the GUI."""

from run_gui import main


if __name__ == "__main__":
    raise SystemExit(main())
