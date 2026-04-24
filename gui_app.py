import sys
import os
import socket
import ipaddress
import concurrent.futures
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
    QFileDialog, QLabel, QLineEdit, QComboBox, QProgressBar, QStyle, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
import json
import platform


class NetworkScanner(QThread):
    """Scans the local /24 subnet for live hosts and guesses their OS."""
    scan_complete = pyqtSignal(list)  # list of (ip, os_type, label)
    scan_status   = pyqtSignal(str)   # progress messages

    # Port -> OS hint, checked in priority order
    OS_PORTS = [
        (445,  "Windows"),  # SMB
        (3389, "Windows"),  # RDP
        (22,   "Linux"),    # SSH
    ]

    def run(self):
        self.scan_status.emit("Scanning network…")
        local_ip = self._get_local_ip()
        if not local_ip:
            self.scan_status.emit("Could not determine local IP.")
            self.scan_complete.emit([])
            return

        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        hosts   = [str(h) for h in network.hosts() if str(h) != local_ip]

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
            futures = {executor.submit(self._probe_host, ip): ip for ip in hosts}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        results.sort(key=lambda x: list(map(int, x[0].split("."))))
        self.scan_status.emit(f"Scan complete — {len(results)} host(s) found.")
        self.scan_complete.emit(results)

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _probe_host(self, ip):
        os_type = "Unknown"
        alive   = False

        for port, hint in self.OS_PORTS:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.4)
                if s.connect_ex((ip, port)) == 0:
                    alive   = True
                    os_type = hint
                    s.close()
                    break
                s.close()
            except Exception:
                pass

        if not alive:
            return None

        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            hostname = ip

        label = f"{ip}  ({hostname})  [{os_type}]"
        return (ip, os_type, label)


class SyncApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Sync Tool")
        self.setGeometry(200, 200, 1000, 700)

        self.settings_file  = self.get_settings_file_path()
        self.game_defaults  = {}
        self.previous_paths = {}
        self.scanned_hosts  = []  # list of (ip, os_type, label)
        self.local_os       = "Linux" if platform.system() != "Windows" else "Windows"

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.setup_darker_theme()
        self.init_ui()
        self.load_game_defaults()
        self.load_settings()
        self._apply_local_os_source_path()

        self.scan_active = False
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(60_000)
        self.scan_timer.timeout.connect(self.start_network_scan)
        self.scan_timer.start()
        self.start_network_scan()

    # ── Window helpers ────────────────────────────────────────────────────────

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

    # ── Theme ─────────────────────────────────────────────────────────────────

    def setup_darker_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,      QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText,  Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base,        QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.Text,        Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button,      QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText,  Qt.GlobalColor.white)
        self.setPalette(palette)

    # ── UI ────────────────────────────────────────────────────────────────────

    def init_ui(self):
        widget = QWidget()
        main_layout = QVBoxLayout()

        # ── Title Header ──────────────────────────────────────────────────────
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

        # ── Local machine info ────────────────────────────────────────────────
        self.local_os_label = QLabel(f"Local machine OS: {self.local_os}")
        self.local_os_label.setStyleSheet("font-size: 11px; color: lightblue;")
        main_layout.addWidget(self.local_os_label)

        # ── Game Selection ────────────────────────────────────────────────────
        self.select_game_label = QLabel("Select Game:")
        main_layout.addWidget(self.select_game_label)

        self.game_dropdown = QComboBox()
        self.game_dropdown.currentIndexChanged.connect(self.update_paths)
        main_layout.addWidget(self.game_dropdown)

        # ── Network Scan / Destination Machine ───────────────────────────────
        dest_machine_label = QLabel("Destination Machine (Network Scan):")
        main_layout.addWidget(dest_machine_label)

        scan_row = QHBoxLayout()

        self.scan_dropdown = QComboBox()
        self.scan_dropdown.addItem("— select a destination machine —")
        self.scan_dropdown.currentIndexChanged.connect(self.on_destination_selected)
        self.scan_dropdown.setEnabled(False)
        scan_row.addWidget(self.scan_dropdown)

        self.scan_button = QPushButton("Scan Network")
        self.scan_button.setFixedWidth(120)
        self.scan_button.clicked.connect(self.start_network_scan)
        scan_row.addWidget(self.scan_button)

        main_layout.addLayout(scan_row)

        self.scan_status_label = QLabel("")
        self.scan_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        main_layout.addWidget(self.scan_status_label)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setVisible(False)
        self.scan_progress.setFixedHeight(12)
        self.scan_progress.setTextVisible(False)
        main_layout.addWidget(self.scan_progress)

        # ── Sync Direction ────────────────────────────────────────────────────
        self.sync_direction_label = QLabel("Sync Direction:")
        main_layout.addWidget(self.sync_direction_label)

        self.sync_direction_dropdown = QComboBox()
        self.sync_direction_dropdown.addItems([
            "Linux ↔ Linux",
            "Linux ↔ Windows",
            "Windows ↔ Linux",
            "Windows ↔ Windows",
        ])
        self.sync_direction_dropdown.currentIndexChanged.connect(self.update_paths)
        main_layout.addWidget(self.sync_direction_dropdown)

        # ── Source Path ───────────────────────────────────────────────────────
        self.source_label = QLabel("Source Path (this machine):")
        main_layout.addWidget(self.source_label)

        self.source_path = QLineEdit()
        main_layout.addWidget(self.source_path)

        # ── Destination Path ──────────────────────────────────────────────────
        self.dest_label = QLabel("Destination Path (remote machine):")
        main_layout.addWidget(self.dest_label)

        self.dest_path = QLineEdit()
        main_layout.addWidget(self.dest_path)

        # ── Sync Button ───────────────────────────────────────────────────────
        self.sync_button = QPushButton("Start Sync")
        self.sync_button.clicked.connect(self.start_sync)

        warning_label = QLabel("Syncing large game files may take time. Please be patient and do not interrupt the process.")
        warning_label.setStyleSheet("font-size: 10px; color: orange;")
        warning_label.setWordWrap(False)
        warning_label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
        main_layout.addWidget(warning_label, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.sync_button, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── Progress Bar ──────────────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        widget.setLayout(main_layout)
        self.setCentralWidget(widget)

    # ── Network scan ──────────────────────────────────────────────────────────

    def start_network_scan(self):
        if self.scan_active:
            return

        self.scan_active = True
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning...")
        self.scan_dropdown.clear()
        self.scan_dropdown.addItem("Scanning…")
        self.scan_dropdown.setEnabled(False)
        self.scan_status_label.setText("Scanning LAN for hosts…")
        self.scan_progress.setVisible(True)
        self.scanned_hosts = []

        self.scanner = NetworkScanner()
        self.scanner.scan_status.connect(self.scan_status_label.setText)
        self.scanner.scan_complete.connect(self.on_scan_complete)
        self.scanner.start()

    def on_scan_complete(self, hosts):
        self.scan_active = False
        self.scanned_hosts = hosts
        self.scan_dropdown.clear()
        self.scan_dropdown.addItem("— select a destination machine —")
        for _ip, _os, label in hosts:
            self.scan_dropdown.addItem(label)
        self.scan_button.setEnabled(True)
        self.scan_button.setText("Scan Network")
        self.scan_progress.setVisible(False)
        self.scan_dropdown.setEnabled(bool(hosts))

    def on_destination_selected(self, index):
        """Auto-set sync direction and paths when a scanned machine is selected."""
        if index <= 0 or index > len(self.scanned_hosts):
            return
        _ip, remote_os, _label = self.scanned_hosts[index - 1]
        self._set_sync_direction(self.local_os, remote_os)
        self.update_paths()

    # ── OS / path helpers ─────────────────────────────────────────────────────

    def _apply_local_os_source_path(self):
        """Pre-fill source path from game defaults based on the local OS."""
        game_name = self.game_dropdown.currentText()
        if not game_name or game_name not in self.game_defaults:
            return
        defaults = self.game_defaults[game_name]
        key = "linux" if self.local_os == "Linux" else "windows"
        if not self.previous_paths.get("source_path"):
            self.source_path.setText(defaults.get(key, ""))

    def _set_sync_direction(self, local_os, remote_os):
        """Pick the matching sync direction item from the dropdown."""
        label = f"{local_os} ↔ {remote_os}"
        idx = self.sync_direction_dropdown.findText(label)
        if idx >= 0:
            self.sync_direction_dropdown.blockSignals(True)
            self.sync_direction_dropdown.setCurrentIndex(idx)
            self.sync_direction_dropdown.blockSignals(False)

    # ── Data / settings ───────────────────────────────────────────────────────

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
        selected_game      = self.game_dropdown.currentText()
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

        # Restore any user-saved overrides
        if self.previous_paths.get("source_path"):
            self.source_path.setText(self.previous_paths["source_path"])
        if self.previous_paths.get("dest_path"):
            self.dest_path.setText(self.previous_paths["dest_path"])

    def save_settings(self):
        settings = {
            "game":           self.game_dropdown.currentText(),
            "sync_direction": self.sync_direction_dropdown.currentText(),
            "source_path":    self.source_path.text(),
            "dest_path":      self.dest_path.text(),
        }
        try:
            with open(self.settings_file, "w") as f:
                json.dump(settings, f)
        except Exception as err:
            print(f"Could not save settings: {err}")

    def start_sync(self):
        source      = self.source_path.text()
        destination = self.dest_path.text()
        print(f"Syncing from {source} to {destination}")
        self.save_settings()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(50)
        self.progress_bar.setValue(100)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SyncApp()
    window.show()
    sys.exit(app.exec())
