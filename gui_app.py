import sys
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
    QFileDialog, QLabel, QLineEdit, QComboBox, QProgressBar
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
import subprocess
import json
import platform

class SyncApp(QMainWindow):
    SETTINGS_FILE = os.path.expanduser("~/.zomboid_sync_settings.json")

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Sync Tool")
        self.setGeometry(200, 200, 1000, 700)

        self.setup_darker_theme()
        self.init_ui()
        self.load_settings()

    def setup_darker_theme(self):
        palette = QPalette()

        # Set dark gray background
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(66, 66, 66))
        palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)

        self.setPalette(palette)

    def init_ui(self):
        widget = QWidget()
        main_layout = QVBoxLayout()

        # Header: Title and Top-Right Buttons
        header_layout = QHBoxLayout()

        title_label = QLabel("Game Sync Tool")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        header_layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)

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

        # Subtitle
        subtitle_label = QLabel("Sync your games across LAN machines with ease")
        subtitle_label.setStyleSheet("font-size: 16px; color: lightgray;")
        main_layout.addWidget(subtitle_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Header Description, Notes, Warnings, Tips
        description_label = QLabel("Select your game, choose the destination machine, and start syncing your game files effortlessly.")
        description_label.setStyleSheet("font-size: 14px; color: white;")
        main_layout.addWidget(description_label)

        note_label = QLabel("Ensure both machines are connected to the same LAN network for optimal performance.")
        note_label.setStyleSheet("font-size: 12px; color: lightgreen;")
        main_layout.addWidget(note_label)

        # warning_label = QLabel("Syncing large game files may take time. Please be patient and do not interrupt the process.")
        # warning_label.setStyleSheet("font-size: 12px; color: yellow;")
        # main_layout.addWidget(warning_label)

        tip_label = QLabel("Use the 'Sync Direction' option to specify whether you want to sync from the source machine to the destination machine or vice versa.")
        tip_label.setStyleSheet("font-size: 12px; color: lightblue;")
        main_layout.addWidget(tip_label)

        # Group 1: Game, Destination Machine, and Sync Direction
        group1_layout = QVBoxLayout()

        self.game_select_label = QLabel("Select Game:")
        group1_layout.addWidget(self.game_select_label)

        self.game_select_dropdown = QComboBox()
        self.game_select_dropdown.currentIndexChanged.connect(self.update_paths)
        group1_layout.addWidget(self.game_select_dropdown)

        self.lan_machines_label = QLabel("Destination: Select LAN Machine")
        group1_layout.addWidget(self.lan_machines_label)

        self.lan_machine_dropdown = QComboBox()
        group1_layout.addWidget(self.lan_machine_dropdown)

        self.sync_direction_label = QLabel("Sync Direction:")
        group1_layout.addWidget(self.sync_direction_label)

        self.sync_direction_dropdown = QComboBox()
        self.sync_direction_dropdown.addItems([
            "Local (Windows/Linux) -> LAN",
            "LAN -> Local (Windows/Linux)",
            "Windows -> Linux",
            "Linux -> Windows",
            "Windows -> Windows"
        ])
        self.sync_direction_dropdown.currentIndexChanged.connect(self.update_paths)
        group1_layout.addWidget(self.sync_direction_dropdown)

        main_layout.addLayout(group1_layout)

        # Spacer between groups
        main_layout.addStretch(1)

        # Group 2: Source and Destination Paths
        group2_layout = QVBoxLayout()

        self.source_label = QLabel("Source Path (Local Machine):")
        group2_layout.addWidget(self.source_label)

        self.source_path = QLineEdit()
        group2_layout.addWidget(self.source_path)

        self.dest_label = QLabel("Destination Path (LAN Machine):")
        group2_layout.addWidget(self.dest_label)

        self.dest_path = QLineEdit()
        group2_layout.addWidget(self.dest_path)

        main_layout.addLayout(group2_layout)

        # Spacer between groups
        main_layout.addStretch(1)

        # Footer: Buttons, Warning, and Progress Bar
        footer_layout = QVBoxLayout()

        sync_warning_label = QLabel("Syncing large game files may take time. Please ensure uninterrupted network connectivity.")
        sync_warning_label.setStyleSheet("font-size: 12px; color: orange;")
        footer_layout.addWidget(sync_warning_label)

        self.sync_button = QPushButton("Start Sync")
        self.sync_button.clicked.connect(self.start_sync)
        footer_layout.addWidget(self.sync_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.hide()  # Default hidden, displayed only when sync starts
        footer_layout.addWidget(self.progress_bar)

        main_layout.addLayout(footer_layout)

        widget.setLayout(main_layout)
        self.setCentralWidget(widget)

        self.load_game_defaults()
        self.scan_and_populate_lan_machines()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def scan_and_populate_lan_machines(self):
        """ Mock discovery for LAN machines (replace with real logic) """
        devices = ["192.168.1.2 (LinuxServer)", "192.168.1.3 (WindowsMachine)"]
        self.lan_machine_dropdown.addItems(devices)

    def load_game_defaults(self):
        self.game_defaults = {}
        defaults_file = Path(__file__).parent / "game_defaults.json"

        try:
            with open(defaults_file, "r") as f:
                data = json.load(f)
                self.game_defaults = {game["name"]: game["defaults"] for game in data["games"]}
                self.game_select_dropdown.addItems(self.game_defaults.keys())
        except Exception as e:
            print(f"Could not load defaults: {e}")

    def update_paths(self):
        game_name = self.game_select_dropdown.currentText()
        sync_direction = self.sync_direction_dropdown.currentText()

        if not game_name or game_name not in self.game_defaults:
            return

        defaults = self.game_defaults[game_name]
        current_os = platform.system()

        if sync_direction.startswith("Local"):
            if "Linux" in current_os:
                self.source_path.setText(defaults.get("linux", ""))
            elif "Windows" in current_os:
                self.source_path.setText(defaults.get("windows", ""))
            self.dest_path.setText("")

    def load_settings(self):
        if Path(self.SETTINGS_FILE).exists():
            try:
                with open(self.SETTINGS_FILE, "r") as f:
                    settings = json.load(f)
                    self.game_select_dropdown.setCurrentText(settings.get("game", ""))
                    self.sync_direction_dropdown.setCurrentText(settings.get("sync_direction", ""))
            except Exception as e:
                print(f"Could not load settings: {e}")

    def save_settings(self):
        settings = {
            "game": self.game_select_dropdown.currentText(),
            "sync_direction": self.sync_direction_dropdown.currentText()
        }
        try:
            with open(self.SETTINGS_FILE, "w") as f:
                json.dump(settings, f)
        except Exception as e:
            print(f"Could not save settings: {e}")

    def start_sync(self):
        source = self.source_path.text()
        destination = self.dest_path.text()
        print(f"Starting sync from {source} to {destination}")
        self.progress_bar.show()
        # Add syncing logic here

if __name__ == "__main__":
    app = QApplication(sys.argv)
    sync_window = SyncApp()
    sync_window.show()
    sys.exit(app.exec())