import sys

from PyQt6.QtWidgets import QApplication

from ui_app import SyncApp


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SyncApp()
    window.show()
    sys.exit(app.exec())
