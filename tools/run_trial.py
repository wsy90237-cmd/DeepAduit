"""Start this checkout for local trials using dependencies already installed here."""

from pathlib import Path
import site
import sys


ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    for directory in (ROOT / ".venv/Lib/site-packages",
                      ROOT / "local-data/tb-study-20261029-1024/deps"):
        if directory.is_dir():
            site.addsitedir(str(directory))
    try:
        import win32crypt  # noqa: F401 -- fail early if Windows support is missing
        from tb_autofill.gui import main as start_gui
    except ImportError as exc:
        raise SystemExit(f"Missing runtime dependency: {exc}. Install requirements.txt with this Python first.") from exc
    start_gui()


if __name__ == "__main__":
    main()
