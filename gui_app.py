import sys
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
    QFileDialog, QLabel, QLineEdit, QComboBox, QProgressBar, QStyle, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
import subprocess
import json
import platform

class SyncApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Sync Tool")
        self.setGeometry(200, 200, 1000, 700)

        self.settings_file = self.get_settings_file_path()
        self.game_defaults = {}
        self.previous_paths = {}

        # Add standard window controls
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.setup_darker_theme()
        self.init_ui()
        self.load_game_defaults()
        self.load_settings()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def get_settings_file_path(self):
        if platform.system() == "Windows":
            return Path(os.getenv("APPDATA", "~")) / "zomboid_sync_settings.json"
        else:
            return Path.home() / ".zomboid_sync_settings.json"

    def setup_darker_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        self.setPalette(palette)

    def init_ui(self):
        widget = QWidget()
        
        main_layout = QVBoxLayout()

        # Title Header
        header_layout = QHBoxLayout()
        header_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        header_layout.addStretch(1)

        header_label = QLabel("Game Sync Tool")
        header_label.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        header_layout.addWidget(header_label, alignment=Qt.AlignmentFlag.AlignCenter)

        header_layout.addStretch(1)

        window_control_layout = QHBoxLayout()
        window_control_layout.setAlignment(Qt.AlignmentFlag.AlignRight)

        minimize_button = QPushButton("_", self)
        minimize_button.setFixedSize(30, 20)
        minimize_button.clicked.connect(self.showMinimized)
        window_control_layout.addWidget(minimize_button)

        maximize_button = QPushButton("[ ]", self)
        maximize_button.setFixedSize(30, 20)
        maximize_button.clicked.connect(self.toggle_maximize)
        window_control_layout.addWidget(maximize_button)

        close_button = QPushButton("X", self)
        close_button.setFixedSize(30, 20)
        close_button.clicked.connect(self.close)
        window_control_layout.addWidget(close_button)

        header_layout.addLayout(window_control_layout)
        main_layout.addLayout(header_layout)

        description_label = QLabel("Select your game, choose the destination machine, and start syncing your game files effortlessly.")
        description_label.setStyleSheet("font-size: 12px; color: gray;")
        main_layout.addWidget(description_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Game Selection Dropdown
        self.select_game_label = QLabel("Select Game:")
        main_layout.addWidget(self.select_game_label)

        self.game_dropdown = QComboBox()
        self.game_dropdown.currentIndexChanged.connect(self.update_paths)
        main_layout.addWidget(self.game_dropdown)

        # Sync Direction Dropdown
        self.sync_direction_label = QLabel("Sync Direction:")
        main_layout.addWidget(self.sync_direction_label)

        self.sync_direction_dropdown = QComboBox()
        self.sync_direction_dropdown.addItems(["Linux ↔ Linux", "Linux ↔ Windows", "Windows ↔ Linux", "Windows ↔ Windows"])
        self.sync_direction_dropdown.currentIndexChanged.connect(self.update_paths)
        main_layout.addWidget(self.sync_direction_dropdown)

        # Source Path
        self.source_label = QLabel("Source Path:")
        main_layout.addWidget(self.source_label)

        self.source_path = QLineEdit()
        main_layout.addWidget(self.source_path)

        # Destination Path
        self.dest_label = QLabel("Destination Path:")
        main_layout.addWidget(self.dest_label)

        self.dest_path = QLineEdit()
        main_layout.addWidget(self.dest_path)

        # Sync Button
        self.sync_button = QPushButton("Start Sync")
        self.sync_button.clicked.connect(self.start_sync)
        # Add warning message near Start Sync button
        warning_label = QLabel("Syncing large game files may take time. Please be patient and do not interrupt the process.")
        warning_label.setStyleSheet("font-size: 10px; color: orange;")
        warning_label.setWordWrap(False)
        warning_label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
        main_layout.addWidget(warning_label, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.sync_button, alignment=Qt.AlignmentFlag.AlignCenter)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        widget.setLayout(main_layout)
        self.setCentralWidget(widget)

    def load_game_defaults(self):
        try:
            defaults_path = Path(__file__).parent / "game_defaults.json"
            with open(defaults_path, "r") as f:
                data = json.load(f)
                for game in data["games"]:
                    self.game_defaults[game["name"]] = game["defaults"]
            self.game_dropdown.addItems(self.game_defaults.keys())
        except Exception as err:
            print(f"Error loading game defaults: {err}")

    def load_settings(self):
        if not self.settings_file.exists():
            return

        try:
            with open(self.settings_file, "r") as f:
                self.previous_paths = json.load(f)
                game = self.previous_paths.get("game")
                if game:
                    self.game_dropdown.setCurrentText(game)
                sync_direction = self.previous_paths.get("sync_direction")
                if sync_direction:
                    self.sync_direction_dropdown.setCurrentText(sync_direction)
                self.source_path.setText(self.previous_paths.get("source_path", ""))
                self.dest_path.setText(self.previous_paths.get("dest_path", ""))
        except Exception as err:
            print(f"Could not load settings: {err}")

    def update_paths(self):
        selected_game = self.game_dropdown.currentText()
        selected_direction = self.sync_direction_dropdown.currentText()

        if not selected_game:
            return

        defaults = self.game_defaults.get(selected_game, {})
        if selected_direction == "Linux ↔ Linux":
            self.source_path.setText(defaults.get("linux", ""))
            self.dest_path.setText(defaults.get("linux", ""))
        elif selected_direction == "Linux ↔ Windows":
            self.source_path.setText(defaults.get("linux", ""))
            self.dest_path.setText(defaults.get("windows", ""))
        elif selected_direction == "Windows ↔ Linux":
            self.source_path.setText(defaults.get("windows", ""))
            self.dest_path.setText(defaults.get("linux", ""))
        elif selected_direction == "Windows ↔ Windows":
            self.source_path.setText(defaults.get("windows", ""))
            self.dest_path.setText(defaults.get("windows", ""))

        # Override with any saved user paths
        self.source_path.setText(self.previous_paths.get("source_path", self.source_path.text()))
        self.dest_path.setText(self.previous_paths.get("dest_path", self.dest_path.text()))

    def save_settings(self):
        settings = {
            "game": self.game_dropdown.currentText(),
            "sync_direction": self.sync_direction_dropdown.currentText(),
            "source_path": self.source_path.text(),
            "dest_path": self.dest_path.text(),
            "note": "Ensure both machines are connected to the same LAN network for optimal performance."
        }

        try:
            with open(self.settings_file, "w") as f:
                json.dump(settings, f)
        except Exception as err:
            print(f"Could not save settings: {err}")

    def start_sync(self):
        source = self.source_path.text()
        destination = self.dest_path.text()
        print(f"Syncing from {source} to {destination}")

        # Save progress when sync is triggered
        self.save_settings()

        # Placeholder sync logic
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(50)  # Simulate progress (replace with actual logic)
        self.progress_bar.setValue(100)  # Complete sync

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SyncApp()
    window.show()
    sys.exit(app.exec())