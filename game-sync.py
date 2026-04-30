import sys

from PyQt6.QtWidgets import QApplication

from app_version import APP_VERSION
from ui_app import SyncApp


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(APP_VERSION)
        raise SystemExit(0)

    app = QApplication(sys.argv)
    window = SyncApp()
    window.show()
    sys.exit(app.exec())
