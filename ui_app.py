import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

# Suppress console-window flicker when running as a Windows EXE
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resource_path(relative: str) -> str:
    """Return absolute path to a bundled resource, works for PyInstaller EXE and dev."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    KEYRING_AVAILABLE = False

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cloud_sync import CloudWorkerThread, RcloneSync, rclone_is_available
from game_defaults import GAME_DEFAULTS
from local_network_sync import (
    ConnectionTestThread,
    DirectSyncWorkerThread,
    LocalNetworkSync,
)
from network_scanner import NetworkScanner


# ── Emulator / custom game templates ─────────────────────────────────────────

_EMULATOR_TEMPLATES: list[dict] = [
    {
        "label": "Custom (enter paths manually)",
        "windows": "",
        "linux": "",
    },
    # ── RetroArch ──────────────────────────────────────────────────────────
    {
        "label": "RetroArch (any system)",
        "windows": r"%APPDATA%\RetroArch\saves",
        "linux": "~/.config/retroarch/saves",
    },
    {
        "label": "RetroArch – EmuDeck layout",
        "windows": r"%APPDATA%\RetroArch\saves",
        "linux": "~/Emulation/saves/retroarch/saves",
    },
    # ── Standalone emulators ───────────────────────────────────────────────
    {
        "label": "Dolphin (GameCube / Wii)",
        "windows": r"%USERPROFILE%\Documents\Dolphin Emulator\GC",
        "linux": "~/.local/share/dolphin-emu/GC",
    },
    {
        "label": "Dolphin – EmuDeck layout",
        "windows": r"%USERPROFILE%\Documents\Dolphin Emulator\GC",
        "linux": "~/Emulation/saves/dolphin-emu/GC",
    },
    {
        "label": "DuckStation (PS1)",
        "windows": r"%APPDATA%\DuckStation\memcards",
        "linux": "~/.local/share/duckstation/memcards",
    },
    {
        "label": "DuckStation – EmuDeck layout",
        "windows": r"%APPDATA%\DuckStation\memcards",
        "linux": "~/Emulation/saves/duckstation/memcards",
    },
    {
        "label": "PCSX2 (PS2)",
        "windows": r"%USERPROFILE%\Documents\PCSX2\memcards",
        "linux": "~/.config/PCSX2/memcards",
    },
    {
        "label": "PCSX2 – EmuDeck layout",
        "windows": r"%USERPROFILE%\Documents\PCSX2\memcards",
        "linux": "~/Emulation/saves/PCSX2/memcards",
    },
    {
        "label": "RPCS3 (PS3)",
        "windows": r"%APPDATA%\rpcs3\dev_hdd0\home\00000001\savedata",
        "linux": "~/.config/rpcs3/dev_hdd0/home/00000001/savedata",
    },
    {
        "label": "RPCS3 – EmuDeck layout",
        "windows": r"%APPDATA%\rpcs3\dev_hdd0\home\00000001\savedata",
        "linux": "~/Emulation/saves/rpcs3/dev_hdd0/home/00000001/savedata",
    },
    {
        "label": "Yuzu / Ryujinx (Nintendo Switch)",
        "windows": r"%APPDATA%\yuzu\nand\user\save",
        "linux": "~/.local/share/yuzu/nand/user/save",
    },
    {
        "label": "Yuzu – EmuDeck layout",
        "windows": r"%APPDATA%\yuzu\nand\user\save",
        "linux": "~/Emulation/saves/yuzu/nand/user/save",
    },
    {
        "label": "Citra (Nintendo 3DS)",
        "windows": r"%APPDATA%\Citra\sdmc\Nintendo 3DS",
        "linux": "~/.local/share/citra-emu/sdmc/Nintendo 3DS",
    },
    {
        "label": "PPSSPP (PSP)",
        "windows": r"%APPDATA%\PPSSPP\PSP\SAVEDATA",
        "linux": "~/.config/ppsspp/PSP/SAVEDATA",
    },
    {
        "label": "mGBA (GBA)",
        "windows": r"%APPDATA%\mGBA\saves",
        "linux": "~/.config/mgba/saves",
    },
    {
        "label": "EmuDeck – generic saves folder",
        "windows": "",
        "linux": "~/Emulation/saves",
    },
]


class _CustomGameDialog(QDialog):
    """Dialog to add a custom / emulator game entry."""

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Custom Game" if not existing else "Edit Custom Game")
        self.setMinimumWidth(560)
        self.setStyleSheet(
            "QDialog, QWidget { background-color: #353535; color: white; }"
            "QLineEdit, QComboBox { background-color: #3f3f3f; color: white;"
            "  border: 1px solid #555; padding: 2px 4px; }"
            "QPushButton { background-color: #444; color: white; border: 1px solid #555;"
            "  padding: 3px 8px; }"
            "QPushButton:hover { background-color: #5a5a5a; }"
            "QLabel { color: white; }"
            "QDialogButtonBox QPushButton { min-width: 70px; }"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(6)

        # ── Game name ──────────────────────────────────────────────────────
        self.name_edit = QLineEdit(existing.get("name", "") if existing else "")
        self.name_edit.setPlaceholderText("e.g. My Retro Game")
        form.addRow("Game name:", self.name_edit)

        # ── Template helper ────────────────────────────────────────────────
        self.template_combo = QComboBox()
        for t in _EMULATOR_TEMPLATES:
            self.template_combo.addItem(t["label"])
        self.template_combo.currentIndexChanged.connect(self._apply_template)
        form.addRow("Emulator / template:", self.template_combo)

        layout.addLayout(form)

        # ── Windows path ───────────────────────────────────────────────────
        win_label = QLabel("Windows save path:")
        layout.addWidget(win_label)
        win_row = QHBoxLayout()
        self.windows_edit = QLineEdit(existing.get("windows", "") if existing else "")
        self.windows_edit.setPlaceholderText(
            r"e.g. %USERPROFILE%\Documents\MyGame\Saves"
        )
        win_row.addWidget(self.windows_edit)
        win_browse = QPushButton("Browse…")
        win_browse.setFixedWidth(80)
        win_browse.clicked.connect(lambda: self._browse(self.windows_edit))
        win_row.addWidget(win_browse)
        layout.addLayout(win_row)

        # ── Linux / SteamDeck path ─────────────────────────────────────────
        lin_label = QLabel("Linux / SteamDeck save path:")
        layout.addWidget(lin_label)
        lin_row = QHBoxLayout()
        self.linux_edit = QLineEdit(existing.get("linux", "") if existing else "")
        self.linux_edit.setPlaceholderText("e.g. ~/MyGame/saves")
        lin_row.addWidget(self.linux_edit)
        lin_browse = QPushButton("Browse…")
        lin_browse.setFixedWidth(80)
        lin_browse.clicked.connect(lambda: self._browse(self.linux_edit))
        lin_row.addWidget(lin_browse)
        layout.addLayout(lin_row)

        hint = QLabel(
            "💡  Pick a template above to auto-fill common emulator paths, then "
            "refine them as needed.  Leave a path blank if that OS is not used."
        )
        hint.setStyleSheet("font-size: 10px; color: #aaa;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── Buttons ────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_template(self, idx: int):
        t = _EMULATOR_TEMPLATES[idx]
        if t["windows"]:
            self.windows_edit.setText(t["windows"])
        if t["linux"]:
            self.linux_edit.setText(t["linux"])

    def _browse(self, target: QLineEdit):
        start = str(Path.home())
        txt = target.text().strip()
        if txt:
            try:
                p = Path(
                    txt.replace("%USERPROFILE%", str(Path.home()))
                    .replace("%APPDATA%", str(Path.home() / "AppData" / "Roaming"))
                ).expanduser()
                candidate = p if p.is_dir() else p.parent
                if candidate.exists():
                    start = str(candidate)
            except Exception:
                pass
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", start)
        if folder:
            target.setText(folder)

    def _validate_and_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a game name.")
            return
        self.accept()

    def result_data(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "windows": self.windows_edit.text().strip(),
            "linux": self.linux_edit.text().strip(),
        }


class SyncApp(QMainWindow):
    # Signals emitted from rclone auth worker thread → main thread
    _rclone_auth_ok = pyqtSignal(str)  # provider
    _rclone_auth_err = pyqtSignal(str, str)  # provider, message
    _rclone_auth_token = pyqtSignal(str, str)  # provider, token_json

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Sync Tool")
        self.setWindowIcon(QIcon(resource_path("app_icon.png")))
        self.setGeometry(200, 200, 1000, 700)

        self.settings_file = self.get_settings_file_path()
        self.game_defaults = {}

        # Wire rclone auth signals (must be done before init_ui)
        self._rclone_auth_token.connect(self._apply_rclone_token)
        self._rclone_auth_ok.connect(self._on_rclone_authorized)
        self._rclone_auth_err.connect(self._on_rclone_auth_error)
        self.previous_paths = {}
        self.scanned_hosts = []  # list of (ip, os_type, label, mac, is_local)
        self._current_dest_mac = ""  # MAC of currently-selected destination
        self._current_dest_ip = ""  # IP  of currently-selected destination
        self.local_os = "Linux" if platform.system() != "Windows" else "Windows"
        self.local_interfaces, self.local_ips, self.local_macs = (
            self._get_local_network_identity()
        )

        self.scan_active = False
        self.sync_active = False
        self.scan_performed = False
        self._loading = False

        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
        )
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        self.setup_darker_theme()
        self.init_ui()
        self.load_game_defaults()

        # ── Cloud state (must be before load_settings so tokens are preserved) ──
        self.rclone_gdrive: RcloneSync | None = None
        self.rclone_dropbox: RcloneSync | None = None
        self.local_network_sync: LocalNetworkSync | None = None
        self.lm_password: str = ""
        self.cloud_worker: CloudWorkerThread | None = None

        self.load_settings()
        self._apply_local_os_source_path()

        # ── Auto-save on any path/game/direction change ────────────────────────
        self.game_dropdown.currentIndexChanged.connect(
            self._on_game_or_direction_changed
        )
        self.sync_direction_dropdown.currentIndexChanged.connect(
            self._on_game_or_direction_changed
        )
        self.source_path.editingFinished.connect(self.save_settings)
        self.dest_path.editingFinished.connect(self.save_settings)
        self.cloud_folder_input.editingFinished.connect(self.save_settings)

        self.scan_active = False
        self.sync_active = False
        self.scan_performed = False
        self._loading = False
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(60_000)
        self.scan_timer.timeout.connect(self.on_scan_timer_timeout)
        self.scan_timer.start()
        if self._should_auto_scan_network():
            self.start_network_scan()

        self._last_game_selected = self.game_dropdown.currentText()

    # ── Window helpers ────────────────────────────────────────────────────────

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def get_settings_file_path(self):
        game_sync_settings = "game_sync_settings.json"
        if platform.system() == "Windows":
            appdata = os.getenv("APPDATA")
            base = Path(appdata) if appdata else Path.home()
        else:
            base = Path.home()

        return base / game_sync_settings

    def _rclone_token_key(self, provider: str) -> str:
        return f"game-sync.rclone.{provider}"

    def _store_rclone_token(self, provider: str, token: str) -> None:
        """Prefer the OS credential store for sensitive OAuth refresh tokens."""
        if KEYRING_AVAILABLE:
            try:
                keyring.set_password(
                    "Game Sync Tool",
                    self._rclone_token_key(provider),
                    token,
                )
                self.previous_paths[f"rclone_{provider}_token_id"] = (
                    self._rclone_token_key(provider)
                )
                self.previous_paths[f"rclone_{provider}_token"] = ""
                return
            except Exception:
                pass

        self.previous_paths[f"rclone_{provider}_token"] = token
        self.previous_paths.pop(f"rclone_{provider}_token_id", None)

    def _retrieve_rclone_token(self, provider: str) -> str:
        if KEYRING_AVAILABLE:
            token_id = self.previous_paths.get(f"rclone_{provider}_token_id")
            if token_id:
                try:
                    token = keyring.get_password("Game Sync Tool", token_id)
                    if token:
                        return token
                except Exception:
                    pass
        return self.previous_paths.get(f"rclone_{provider}_token", "")

    def _delete_rclone_token(self, provider: str) -> None:
        if KEYRING_AVAILABLE:
            token_id = self.previous_paths.get(f"rclone_{provider}_token_id")
            if token_id:
                try:
                    keyring.delete_password("Game Sync Tool", token_id)
                except Exception:
                    pass
        self.previous_paths.pop(f"rclone_{provider}_token", None)
        self.previous_paths.pop(f"rclone_{provider}_token_id", None)

    def _ensure_secure_settings_permissions(self) -> None:
        if platform.system() == "Windows":
            return
        try:
            if self.settings_file.exists():
                self.settings_file.chmod(0o600)
        except Exception:
            pass

    def _write_settings_file(self, settings: dict) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings, indent=2, ensure_ascii=False)
        if platform.system() == "Windows":
            self.settings_file.write_text(payload, encoding="utf-8")
            return

        fd = os.open(self.settings_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            os.close(fd)
            raise
        try:
            os.chmod(self.settings_file, 0o600)
        except Exception:
            pass

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _get_local_network_identity(self):
        interfaces = []
        local_ips = set()
        local_macs = set()

        if platform.system() == "Windows":
            try:
                result = subprocess.run(
                    ["ipconfig", "/all"],
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=_CREATE_NO_WINDOW,
                )
                current_mac = ""
                for line in result.stdout.splitlines():
                    ls = line.strip()
                    if "Physical Address" in ls:
                        # e.g. "Physical Address. . . . . . . . . : AA-BB-CC-DD-EE-FF"
                        mac_part = ls.split(":", 1)[-1].strip()
                        current_mac = mac_part.replace("-", ":").lower()
                    elif "IPv4 Address" in ls:
                        # e.g. "IPv4 Address. . . . . . . . . . . : 192.168.1.5(Preferred)"
                        ip_part = (
                            ls.split(":", 1)[-1]
                            .strip()
                            .replace("(Preferred)", "")
                            .strip()
                        )
                        if ip_part and ip_part not in local_ips:
                            interfaces.append(
                                {"iface": "local", "ip": ip_part, "mac": current_mac}
                            )
                            local_ips.add(ip_part)
                            if current_mac:
                                local_macs.add(current_mac)
            except Exception:
                pass
        else:
            try:
                result = subprocess.run(
                    ["ip", "-o", "-4", "addr", "show", "up", "scope", "global"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if "inet" not in parts:
                        continue

                    iface = parts[1]
                    ip = parts[parts.index("inet") + 1].split("/")[0]
                    mac_path = Path("/sys/class/net") / iface / "address"
                    mac = ""

                    if mac_path.exists():
                        mac = mac_path.read_text(encoding="utf-8").strip().lower()

                    interfaces.append({"iface": iface, "ip": ip, "mac": mac})
                    local_ips.add(ip)
                    if mac:
                        local_macs.add(mac)
            except Exception:
                pass

        fallback_ip = self._get_local_ip()
        if not interfaces and fallback_ip:
            interfaces.append({"iface": "local", "ip": fallback_ip, "mac": ""})
            local_ips.add(fallback_ip)

        return interfaces, local_ips, local_macs

    def _is_local_machine(self, ip, mac):
        normalized_mac = (mac or "").lower()
        return ip in self.local_ips or (
            normalized_mac and normalized_mac in self.local_macs
        )

    # ── Theme ─────────────────────────────────────────────────────────────────

    def setup_darker_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Highlight, QColor(87, 134, 193))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        self.setPalette(palette)
        self.setStyleSheet(
            "QWidget { background-color: #353535; color: white; }"
            "QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QGroupBox, QRadioButton, QCheckBox {"
            " background-color: #3f3f3f; color: white; border: 1px solid #555; }"
            "QPushButton { background-color: #444; color: white; border: 1px solid #555; }"
            "QPushButton:hover { background-color: #5a5a5a; }"
            "QLabel { color: white; }"
            "QScrollBar:vertical { background: #2b2b2b; width: 10px; }"
            "QScrollBar::handle:vertical { background: #626262; border-radius: 5px; }"
            "QScrollBar::handle:vertical:hover { background: #7a7a7a; }"
        )

    # ── UI ────────────────────────────────────────────────────────────────────

    def init_ui(self):
        outer_widget = QWidget()
        outer_layout = QVBoxLayout(outer_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        header_widget = QWidget()
        header_widget.setStyleSheet(
            "background-color: #2d2d2d; border-bottom: 1px solid #444;"
        )
        header_layout = QHBoxLayout(header_widget)

        # ── Local machine info ────────────────────────────────────────────────
        self.local_os_label = QLabel(f"Local machine OS: {self.local_os}")
        self.local_os_label.setStyleSheet("font-size: 11px; color: lightblue;")
        header_layout.addWidget(self.local_os_label)
        header_layout.setContentsMargins(10, 10, 10, 10)
        header_layout.setSpacing(8)
        header_layout.addStretch(1)

        header_label = QLabel("Game Sync Tool")
        header_label.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        header_layout.addWidget(header_label, alignment=Qt.AlignmentFlag.AlignCenter)
        header_layout.addStretch(1)

        window_control_layout = QHBoxLayout()
        window_control_layout.setAlignment(Qt.AlignmentFlag.AlignRight)

        if platform.system() != "Windows":
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
        outer_layout.addWidget(header_widget)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 5, 10, 5)
        content_layout.setSpacing(5)

        description_label = QLabel(
            "Select your game, choose the destination machine, and start syncing your game files effortlessly."
        )
        description_label.setStyleSheet("font-size: 12px; color: gray;")
        content_layout.addWidget(
            description_label, alignment=Qt.AlignmentFlag.AlignCenter
        )

        # ── Game Selection ────────────────────────────────────────────────────
        self.select_game_label = QLabel("Select Game:")
        content_layout.addWidget(self.select_game_label)

        game_row = QHBoxLayout()
        self.game_dropdown = QComboBox()
        game_row.addWidget(self.game_dropdown, 1)
        self._add_game_btn = QPushButton("➕")
        self._add_game_btn.setFixedWidth(32)
        self._add_game_btn.setToolTip("Add a custom / emulator game")
        self._add_game_btn.clicked.connect(self._add_custom_game)
        game_row.addWidget(self._add_game_btn)
        self._remove_game_btn = QPushButton("🗑")
        self._remove_game_btn.setFixedWidth(32)
        self._remove_game_btn.setToolTip("Remove the selected custom game")
        self._remove_game_btn.clicked.connect(self._remove_custom_game)
        game_row.addWidget(self._remove_game_btn)
        content_layout.addLayout(game_row)

        self.source_label = QLabel("Source Path (this machine):")
        content_layout.addWidget(self.source_label)

        source_row = QHBoxLayout()
        self.source_path = QLineEdit()
        source_row.addWidget(self.source_path)
        source_browse_btn = QPushButton("Browse…")
        source_browse_btn.setFixedWidth(90)
        source_browse_btn.clicked.connect(lambda: self._browse_folder(self.source_path))
        source_row.addWidget(source_browse_btn)
        self.source_default_btn = QPushButton("Default")
        self.source_default_btn.setFixedWidth(90)
        self.source_default_btn.clicked.connect(self._set_default_source_path)
        source_row.addWidget(self.source_default_btn)
        content_layout.addLayout(source_row)
        content_layout.addSpacing(10)
        content_layout.addWidget(
            QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #555;"), 1
        )
        content_layout.addSpacing(10)

        # ── Cloud Storage accordion ───────────────────────────────────────────
        self.cloud_enabled_checkbox = QCheckBox(
            "Enable Cloud Storage (middle-man sync)"
        )
        self.cloud_enabled_checkbox.setStyleSheet(
            "font-size: 11px; color: #9fd3ff; font-weight: bold;"
        )
        self.cloud_enabled_checkbox.toggled.connect(self.toggle_cloud_section)
        content_layout.addWidget(self.cloud_enabled_checkbox)

        self.cloud_section = QGroupBox("Cloud Sync Settings")
        self.cloud_section.setVisible(False)
        self.cloud_section.setStyleSheet(
            "QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 6px; "
            "font-size: 11px; color: #9fd3ff; padding: 8px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )
        cloud_layout = QVBoxLayout()
        cloud_layout.setSpacing(6)

        # ── rclone availability banner ────────────────────────────────────────
        self.rclone_banner = QFrame()
        self.rclone_banner.setStyleSheet(
            "QFrame { background: #3b2a00; border: 1px solid #a06000; border-radius: 4px; padding: 4px; margin-bottom: 6px; }"
        )
        _banner_row = QHBoxLayout()
        _banner_row.setContentsMargins(1, 2, 1, 2)
        _banner_icon = QLabel("⚠")
        _banner_icon.setStyleSheet("color: #ffa500; font-size: 13px;")
        _banner_row.addWidget(_banner_icon)
        _banner_text = QLabel(
            "<b>rclone is not installed.</b>  "
            "Google Drive and Dropbox sync require rclone.  "
            "<a href='https://rclone.org/install/' style='color:#ffa500;'>Download rclone.org/install</a>"
        )
        _banner_text.setOpenExternalLinks(True)
        _banner_text.setStyleSheet(
            "font-size: 10px; color: #ffd080; background: transparent; border: none;"
        )
        _banner_text.setWordWrap(True)
        _banner_row.addWidget(_banner_text, 1)
        _banner_check_btn = QPushButton("↺ Check Again")
        _banner_check_btn.setFixedWidth(100)
        _banner_check_btn.setStyleSheet(
            "QPushButton { background: #5a3a00; color: #ffd080; border: 1px solid #a06000; font-size: 10px; }"
            "QPushButton:hover { background: #7a5000; }"
        )
        _banner_check_btn.clicked.connect(self._refresh_rclone_banner)
        _banner_row.addWidget(_banner_check_btn)
        self.rclone_banner.setLayout(_banner_row)
        self.rclone_banner.setVisible(False)
        cloud_layout.addWidget(self.rclone_banner)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Provider:"))
        self.cloud_provider_group = QButtonGroup(self)
        for idx, name in enumerate(
            [
                "Google Drive",
                "Dropbox",
                "Both (GDrive+Dropbox)",
                "Local Network Machine",
            ]
        ):
            rb = QRadioButton(name)
            rb.setStyleSheet("font-size: 11px;")
            if idx == 0:
                rb.setChecked(True)
            self.cloud_provider_group.addButton(rb, idx)
            provider_row.addWidget(rb)
        provider_row.addStretch()
        cloud_layout.addLayout(provider_row)
        self.cloud_provider_group.idToggled.connect(self.on_cloud_provider_changed)

        self.gdrive_section = QWidget()
        gd_layout = QVBoxLayout()
        gd_layout.setContentsMargins(0, 0, 0, 0)
        gd_layout.setSpacing(4)

        gd_header = QLabel("— Google Drive —")
        gd_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        gd_layout.addWidget(gd_header)

        gd_note = QLabel(
            "Sign in with your Google account — no developer setup required."
        )
        gd_note.setStyleSheet("font-size: 10px; color: #aaa;")
        gd_layout.addWidget(gd_note)

        gd_btn_row = QHBoxLayout()
        self.gd_connect_btn = QPushButton("Authorize Google Drive")
        self.gd_connect_btn.setFixedWidth(180)
        self.gd_connect_btn.clicked.connect(lambda: self._authorize_rclone("gdrive"))
        gd_btn_row.addWidget(self.gd_connect_btn)
        self.gd_logout_btn = QPushButton("Log Out")
        self.gd_logout_btn.setFixedWidth(70)
        self.gd_logout_btn.setStyleSheet("color: #ff8080;")
        self.gd_logout_btn.clicked.connect(lambda: self._logout_rclone("gdrive"))
        self.gd_logout_btn.setVisible(False)
        gd_btn_row.addWidget(self.gd_logout_btn)
        self.gd_status_label = QLabel("Not authorized")
        self.gd_status_label.setStyleSheet("font-size: 10px; color: gray;")
        gd_btn_row.addWidget(self.gd_status_label)
        gd_btn_row.addStretch()
        gd_layout.addLayout(gd_btn_row)

        self.gdrive_section.setLayout(gd_layout)
        cloud_layout.addWidget(self.gdrive_section)

        self.dropbox_section = QWidget()
        self.dropbox_section.setVisible(False)
        db_layout = QVBoxLayout()
        db_layout.setContentsMargins(0, 0, 0, 0)
        db_layout.setSpacing(4)

        db_header = QLabel("— Dropbox —")
        db_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        db_layout.addWidget(db_header)

        db_note = QLabel(
            "Sign in with your Dropbox account — no developer setup required."
        )
        db_note.setStyleSheet("font-size: 10px; color: #aaa;")
        db_layout.addWidget(db_note)

        db_btn_row = QHBoxLayout()
        self.db_connect_btn = QPushButton("Authorize Dropbox")
        self.db_connect_btn.setFixedWidth(180)
        self.db_connect_btn.clicked.connect(lambda: self._authorize_rclone("dropbox"))
        db_btn_row.addWidget(self.db_connect_btn)
        self.db_logout_btn = QPushButton("Log Out")
        self.db_logout_btn.setFixedWidth(70)
        self.db_logout_btn.setStyleSheet("color: #ff8080;")
        self.db_logout_btn.clicked.connect(lambda: self._logout_rclone("dropbox"))
        self.db_logout_btn.setVisible(False)
        db_btn_row.addWidget(self.db_logout_btn)
        self.db_status_label = QLabel("Not authorized")
        self.db_status_label.setStyleSheet("font-size: 10px; color: gray;")
        db_btn_row.addWidget(self.db_status_label)
        db_btn_row.addStretch()
        db_layout.addLayout(db_btn_row)

        self.dropbox_section.setLayout(db_layout)
        cloud_layout.addWidget(self.dropbox_section)

        self.local_machine_section = QWidget()
        self.local_machine_section.setVisible(False)
        lm_layout = QVBoxLayout()
        lm_layout.setContentsMargins(0, 0, 0, 0)
        lm_layout.setSpacing(4)

        lm_header = QLabel("— Local Network Machine —")
        lm_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        lm_layout.addWidget(lm_header)

        lm_host_row = QHBoxLayout()
        lm_host_label = QLabel("Machine:")
        lm_host_label.setFixedWidth(80)
        lm_host_row.addWidget(lm_host_label)
        self.lm_host_dropdown = QComboBox()
        self.lm_host_dropdown.addItem("— select from scanned machines —")
        self.lm_host_dropdown.currentIndexChanged.connect(self._on_lm_host_selected)
        lm_host_row.addWidget(self.lm_host_dropdown)
        lm_layout.addLayout(lm_host_row)

        self.lm_scan_progress = QProgressBar()
        self.lm_scan_progress.setRange(0, 0)
        self.lm_scan_progress.setVisible(False)
        self.lm_scan_progress.setFixedHeight(12)
        self.lm_scan_progress.setTextVisible(False)
        lm_layout.addWidget(self.lm_scan_progress)

        lm_user_row = QHBoxLayout()
        lm_user_label = QLabel("Username:")
        lm_user_label.setFixedWidth(80)
        lm_user_row.addWidget(lm_user_label)
        self.lm_username_input = QLineEdit()
        self.lm_username_input.setPlaceholderText("e.g.  pi  or  user")
        lm_user_row.addWidget(self.lm_username_input)
        lm_layout.addLayout(lm_user_row)

        lm_path_row = QHBoxLayout()
        lm_path_label = QLabel("Remote path:")
        lm_path_label.setFixedWidth(80)
        lm_path_row.addWidget(lm_path_label)
        self.lm_remote_path_input = QLineEdit()
        self.lm_remote_path_input.setPlaceholderText(
            "e.g. /home/user/GameSync/"
            if getattr(self, "lm_detected_os", "Linux") == "Linux"
            else "e.g. C:\\Users\\User\\GameSync\\"
        )
        lm_path_row.addWidget(self.lm_remote_path_input)
        lm_layout.addLayout(lm_path_row)

        lm_key_row = QHBoxLayout()
        lm_key_label = QLabel("SSH key:")
        lm_key_label.setFixedWidth(80)
        lm_key_row.addWidget(lm_key_label)
        self.lm_ssh_key_input = QLineEdit()
        self.lm_ssh_key_input.setPlaceholderText(
            "(optional) path to private key, e.g. ~/.ssh/id_rsa"
        )
        lm_key_row.addWidget(self.lm_ssh_key_input)
        lm_browse_key_btn = QPushButton("Browse")
        lm_browse_key_btn.setFixedWidth(60)
        lm_browse_key_btn.clicked.connect(self._browse_ssh_key)
        lm_key_row.addWidget(lm_browse_key_btn)
        lm_layout.addLayout(lm_key_row)

        lm_port_test_row = QHBoxLayout()
        lm_port_label = QLabel("SSH port:")
        lm_port_label.setFixedWidth(80)
        lm_port_test_row.addWidget(lm_port_label)
        self.lm_port_input = QLineEdit("22")
        self.lm_port_input.setFixedWidth(50)
        lm_port_test_row.addWidget(self.lm_port_input)
        lm_port_test_row.addSpacing(10)
        self.lm_pass_btn = QPushButton("Set Password")
        self.lm_pass_btn.setFixedWidth(110)
        self.lm_pass_btn.clicked.connect(self._set_lm_password)
        lm_port_test_row.addWidget(self.lm_pass_btn)
        self.lm_test_btn = QPushButton("Test Connection")
        self.lm_test_btn.setFixedWidth(130)
        self.lm_test_btn.clicked.connect(self._test_local_machine_connection)
        lm_port_test_row.addWidget(self.lm_test_btn)
        self.lm_status_label = QLabel("Not configured")
        self.lm_status_label.setStyleSheet("font-size: 10px; color: gray;")
        lm_port_test_row.addWidget(self.lm_status_label)
        lm_port_test_row.addStretch()
        lm_layout.addLayout(lm_port_test_row)

        self.local_machine_section.setLayout(lm_layout)
        cloud_layout.addWidget(self.local_machine_section)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #555;")
        cloud_layout.addWidget(sep)

        self.cloud_folder_row = QWidget()
        cloud_folder_layout = QHBoxLayout(self.cloud_folder_row)
        cloud_folder_layout.setContentsMargins(0, 0, 0, 0)
        cloud_folder_layout.addWidget(QLabel("Cloud Folder:"))
        self.cloud_folder_input = QLineEdit()
        self.cloud_folder_input.setPlaceholderText("/GameSync/<GameName>/")
        cloud_folder_layout.addWidget(self.cloud_folder_input)
        cloud_layout.addWidget(self.cloud_folder_row)

        self.cloud_section.setLayout(cloud_layout)
        content_layout.addWidget(self.cloud_section)

        content_layout.addSpacing(10)
        content_layout.addWidget(
            QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #555;"), 1
        )
        content_layout.addSpacing(10)

        self.dest_machine_widget = QWidget()
        dest_machine_layout = QVBoxLayout(self.dest_machine_widget)
        dest_machine_layout.setContentsMargins(0, 0, 0, 0)
        dest_machine_layout.setSpacing(4)

        self.dest_machine_label = QLabel("Destination Machine (Network Scan):")
        dest_machine_layout.addWidget(self.dest_machine_label)

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

        dest_machine_layout.addLayout(scan_row)

        self.scan_status_label = QLabel("")
        self.scan_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        dest_machine_layout.addWidget(self.scan_status_label)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setVisible(False)
        self.scan_progress.setFixedHeight(12)
        self.scan_progress.setTextVisible(False)
        dest_machine_layout.addWidget(self.scan_progress)

        content_layout.addWidget(self.dest_machine_widget)

        self.dest_ssh_section = QWidget()
        self.dest_ssh_section.setVisible(False)
        dest_ssh_layout = QVBoxLayout(self.dest_ssh_section)
        dest_ssh_layout.setContentsMargins(30, 4, 30, 0)
        dest_ssh_layout.setSpacing(4)

        dest_ssh_header = QLabel("— Destination Machine SSH Credentials —")
        dest_ssh_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        dest_ssh_layout.addWidget(dest_ssh_header)

        dest_ssh_user_row = QHBoxLayout()
        dest_ssh_user_label = QLabel("Username:")
        dest_ssh_user_label.setFixedWidth(80)
        dest_ssh_user_row.addWidget(dest_ssh_user_label)
        self.dest_ssh_user_input = QLineEdit()
        self.dest_ssh_user_input.setPlaceholderText("e.g. user or Administrator")
        dest_ssh_user_row.addWidget(self.dest_ssh_user_input)
        dest_ssh_layout.addLayout(dest_ssh_user_row)

        dest_ssh_key_row = QHBoxLayout()
        dest_ssh_key_label = QLabel("SSH Key:")
        dest_ssh_key_label.setFixedWidth(80)
        dest_ssh_key_row.addWidget(dest_ssh_key_label)
        self.dest_ssh_key_input = QLineEdit()
        self.dest_ssh_key_input.setPlaceholderText("(optional) path to private key")
        dest_ssh_key_row.addWidget(self.dest_ssh_key_input)
        dest_ssh_browse_btn = QPushButton("Browse")
        dest_ssh_browse_btn.setFixedWidth(60)
        dest_ssh_browse_btn.clicked.connect(self._browse_dest_ssh_key)
        dest_ssh_key_row.addWidget(dest_ssh_browse_btn)
        dest_ssh_layout.addLayout(dest_ssh_key_row)

        dest_ssh_port_row = QHBoxLayout()
        dest_ssh_port_label = QLabel("SSH Port:")
        dest_ssh_port_label.setFixedWidth(80)
        dest_ssh_port_row.addWidget(dest_ssh_port_label)
        self.dest_ssh_port_input = QLineEdit("22")
        self.dest_ssh_port_input.setFixedWidth(50)
        dest_ssh_port_row.addWidget(self.dest_ssh_port_input)
        dest_ssh_port_row.addSpacing(10)
        self.dest_ssh_pass_btn = QPushButton("Set Password")
        self.dest_ssh_pass_btn.setFixedWidth(110)
        self.dest_ssh_pass_btn.clicked.connect(self._set_dest_password)
        dest_ssh_port_row.addWidget(self.dest_ssh_pass_btn)
        self.dest_ssh_test_btn = QPushButton("Test Connection")
        self.dest_ssh_test_btn.setFixedWidth(130)
        self.dest_ssh_test_btn.clicked.connect(self._test_dest_connection)
        dest_ssh_port_row.addWidget(self.dest_ssh_test_btn)
        self.dest_ssh_status_label = QLabel("Not tested")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: gray;")
        dest_ssh_port_row.addWidget(self.dest_ssh_status_label)
        dest_ssh_port_row.addStretch()
        dest_ssh_layout.addLayout(dest_ssh_port_row)

        self.dest_ssh_progress = QProgressBar()
        self.dest_ssh_progress.setRange(0, 0)
        self.dest_ssh_progress.setVisible(False)
        self.dest_ssh_progress.setFixedHeight(12)
        self.dest_ssh_progress.setTextVisible(False)
        dest_ssh_layout.addWidget(self.dest_ssh_progress)

        content_layout.addWidget(self.dest_ssh_section)

        self.direct_only_top_spacer = QWidget()
        self.direct_only_top_spacer.setFixedHeight(10)
        content_layout.addWidget(self.direct_only_top_spacer)

        self.direct_only_separator = QFrame(frameShape=QFrame.Shape.HLine)
        self.direct_only_separator.setStyleSheet("color: #555;")
        content_layout.addWidget(self.direct_only_separator, 1)

        self.direct_only_bottom_spacer = QWidget()
        self.direct_only_bottom_spacer.setFixedHeight(10)
        content_layout.addWidget(self.direct_only_bottom_spacer)

        def _set_direct_only_sep_visible(enabled_cloud: bool):
            show = not enabled_cloud
            self.direct_only_top_spacer.setVisible(show)
            self.direct_only_separator.setVisible(show)
            self.direct_only_bottom_spacer.setVisible(show)

        _set_direct_only_sep_visible(self.cloud_enabled_checkbox.isChecked())
        self.cloud_enabled_checkbox.toggled.connect(_set_direct_only_sep_visible)

        self.dest_label = QLabel("Destination Path (remote machine):")
        content_layout.addWidget(self.dest_label)

        dest_row = QHBoxLayout()
        self.dest_path = QLineEdit()
        dest_row.addWidget(self.dest_path)
        self.dest_default_btn = QPushButton("Default")
        self.dest_default_btn.setFixedWidth(90)
        self.dest_default_btn.clicked.connect(self._set_default_dest_path)
        dest_row.addWidget(self.dest_default_btn)
        content_layout.addLayout(dest_row)
        self.dest_label.setVisible(False)
        self.dest_path.setVisible(False)
        self.dest_default_btn.setVisible(False)

        self.sync_direction_label = QLabel("Sync Direction:")
        content_layout.addWidget(self.sync_direction_label)

        self.sync_direction_dropdown = QComboBox()
        self.sync_direction_dropdown.addItems(
            [
                "Linux ↔ Linux",
                "Linux ↔ Windows",
                "Windows ↔ Linux",
                "Windows ↔ Windows",
            ]
        )
        content_layout.addWidget(self.sync_direction_dropdown)

        self.sync_direction_label.setVisible(False)
        self.sync_direction_dropdown.setVisible(False)

        self.sync_button = QPushButton("⬆  Push to Dest")
        self.sync_button.setStyleSheet("background-color: #3a5a8a; color: white;")
        self.sync_button.setVisible(False)
        self.sync_button.clicked.connect(self.start_sync)

        self.pull_dest_btn = QPushButton("⬇  Pull from Dest")
        self.pull_dest_btn.setStyleSheet("background-color: #3a6a4a; color: white;")
        self.pull_dest_btn.setVisible(False)
        self.pull_dest_btn.clicked.connect(self.pull_from_dest)

        self.direct_sync_status_label = QLabel("")
        self.direct_sync_status_label.setStyleSheet(
            "font-size: 10px; color: lightgray;"
        )
        self.direct_sync_status_label.setVisible(False)

        self.push_cloud_btn = QPushButton("⬆  Push to Cloud")
        self.push_cloud_btn.setStyleSheet("background-color: #2a5f8a; color: white;")
        self.push_cloud_btn.setVisible(False)
        self.push_cloud_btn.clicked.connect(self.push_to_cloud)

        self.pull_cloud_btn = QPushButton("⬇  Pull from Cloud")
        self.pull_cloud_btn.setStyleSheet("background-color: #2a6b4a; color: white;")
        self.pull_cloud_btn.setVisible(False)
        self.pull_cloud_btn.clicked.connect(self.pull_from_cloud)

        self.cloud_op_status_label = QLabel("")
        self.cloud_op_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.cloud_op_status_label.setVisible(False)

        sync_btn_row = QHBoxLayout()
        sync_btn_row.addWidget(self.pull_dest_btn)
        sync_btn_row.addWidget(self.sync_button)
        sync_btn_row.addWidget(self.pull_cloud_btn)
        sync_btn_row.addWidget(self.push_cloud_btn)
        content_layout.addLayout(sync_btn_row)
        content_layout.addWidget(
            self.cloud_op_status_label, alignment=Qt.AlignmentFlag.AlignCenter
        )
        content_layout.addWidget(
            self.direct_sync_status_label, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self.warning_label = QLabel(
            "Syncing large game files may take time. Please be patient and do not interrupt the process."
        )
        self.warning_label.setStyleSheet("font-size: 10px; color: orange;")
        self.warning_label.setWordWrap(False)
        self.warning_label.setSizePolicy(
            QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        )

        content_layout.addWidget(
            self.warning_label, alignment=Qt.AlignmentFlag.AlignCenter
        )
        self.warning_label.setVisible(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        content_layout.addWidget(self.progress_bar)

        content_widget.setLayout(content_layout)

        content_widget.setStyleSheet("background-color: #353535; color: white;")
        content_widget.setAutoFillBackground(True)

        scroll_area = QScrollArea()
        scroll_area.setWidget(content_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setStyleSheet(
            "QScrollArea { background-color: #353535; border: none; }"
            "QWidget { background-color: transparent; }"
        )

        footer_tabs = QTabWidget()
        footer_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #444; background: #2a2a2a; }"
            "QTabBar::tab { background: #353535; color: #aaa; padding: 4px 12px;"
            "  border: 1px solid #444; border-bottom: none; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #2a2a2a; color: white; }"
            "QTabBar::tab:hover { background: #444; }"
        )

        log_tab = QWidget()
        log_tab.setStyleSheet("background-color: #2a2a2a;")
        log_vbox = QVBoxLayout(log_tab)
        log_vbox.setContentsMargins(4, 4, 4, 4)
        log_vbox.setSpacing(2)
        self.sync_log = QPlainTextEdit()
        self.sync_log.setReadOnly(True)
        self.sync_log.setMaximumBlockCount(1000)
        self.sync_log.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4;"
            " font-family: monospace; font-size: 11px; border: 1px solid #444; }"
        )
        self.sync_log.setMinimumHeight(120)
        log_vbox.addWidget(self.sync_log)
        footer_tabs.addTab(log_tab, "📋  Sync Log")

        settings_tab = QWidget()
        settings_tab.setStyleSheet("background-color: #2a2a2a;")
        settings_outer = QVBoxLayout(settings_tab)
        settings_outer.setContentsMargins(0, 0, 0, 0)
        settings_outer.setSpacing(0)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        settings_scroll.setStyleSheet(
            "QScrollArea { background: #2a2a2a; border: none; }"
            "QWidget { background: transparent; }"
        )
        settings_inner = QWidget()
        settings_inner.setStyleSheet("background-color: #2a2a2a;")
        settings_vbox = QVBoxLayout(settings_inner)
        settings_vbox.setContentsMargins(12, 10, 12, 10)
        settings_vbox.setSpacing(4)

        def _section_header(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #9fd3ff;"
                " padding-top: 6px; padding-bottom: 2px;"
            )
            return lbl

        def _hint(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 10px; color: #888;")
            lbl.setWordWrap(True)
            return lbl

        def _sep() -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setStyleSheet("color: #3d3d3d; margin: 4px 0;")
            return f

        settings_vbox.addWidget(_section_header("🔄  Sync Behaviour"))

        self.settings_confirm_sync_cb = QCheckBox(
            "Ask me to confirm before every push or pull"
        )
        self.settings_confirm_sync_cb.setChecked(False)
        self.settings_confirm_sync_cb.setToolTip(
            "Shows a Yes/No dialog so you don't accidentally overwrite saves."
        )
        self.settings_confirm_sync_cb.toggled.connect(self.save_settings)
        settings_vbox.addWidget(self.settings_confirm_sync_cb)

        self.settings_auto_scan_cb = QCheckBox(
            "Automatically scan network on startup"
        )
        self.settings_auto_scan_cb.setChecked(False)
        self.settings_auto_scan_cb.setToolTip(
            "Only start a LAN scan automatically when this option is enabled."
        )
        self.settings_auto_scan_cb.toggled.connect(self.save_settings)
        settings_vbox.addWidget(self.settings_auto_scan_cb)

        settings_vbox.addWidget(_sep())

        settings_vbox.addWidget(_section_header("🛠  Advanced: Raw Settings File"))
        settings_vbox.addWidget(
            _hint(
                "Power users: edit the raw JSON that is saved to disk.  "
                "Be careful — invalid JSON will be rejected.  "
                "Use 'Reload from disk' to discard any unsaved edits."
            )
        )

        self.settings_json_editor = QPlainTextEdit()
        self.settings_json_editor.setStyleSheet(
            "QPlainTextEdit { background: #1a1a1a; color: #d4d4d4;"
            " font-family: monospace; font-size: 11px; border: 1px solid #555; }"
        )
        self.settings_json_editor.setMinimumHeight(160)
        settings_vbox.addWidget(self.settings_json_editor)

        json_btn_row = QHBoxLayout()
        st_reload_btn = QPushButton("↺  Reload from disk")
        st_reload_btn.setToolTip("Discard edits and reload the file from disk.")
        st_reload_btn.clicked.connect(self._st_reload_json)
        json_btn_row.addWidget(st_reload_btn)

        st_save_json_btn = QPushButton("💾  Save JSON to disk")
        st_save_json_btn.setToolTip("Validate and save the JSON shown above to disk.")
        st_save_json_btn.setStyleSheet(
            "QPushButton { background: #2a4a2a; color: #aaffaa; border: 1px solid #3a6a3a; }"
            "QPushButton:hover { background: #3a6a3a; }"
        )
        st_save_json_btn.clicked.connect(self._st_save_json)
        json_btn_row.addWidget(st_save_json_btn)

        self.st_json_status = QLabel("")
        self.st_json_status.setStyleSheet("font-size: 10px; color: gray;")
        json_btn_row.addWidget(self.st_json_status)
        json_btn_row.addStretch()
        settings_vbox.addLayout(json_btn_row)

        settings_vbox.addStretch()
        settings_scroll.setWidget(settings_inner)
        settings_outer.addWidget(settings_scroll)
        footer_tabs.addTab(settings_tab, "⚙  Settings")

        about_tab = QWidget()
        about_tab.setStyleSheet("background-color: #2a2a2a;")
        about_vbox = QVBoxLayout(about_tab)
        about_vbox.setContentsMargins(16, 10, 16, 10)
        about_vbox.setSpacing(6)

        about_title = QLabel("Game Sync Tool")
        about_title.setStyleSheet("font-size: 16px; font-weight: bold; color: white;")
        about_vbox.addWidget(about_title)

        about_desc = QLabel(
            "Game Sync Tool lets you effortlessly transfer game save files between "
            "machines on your local network or via cloud storage (Google Drive / Dropbox). "
            "It supports Linux ↔ Windows cross-platform syncing and uses rclone for "
            "secure, reliable cloud transfers — no developer accounts required."
        )
        about_desc.setStyleSheet("font-size: 11px; color: #ccc;")
        about_desc.setWordWrap(True)
        about_vbox.addWidget(about_desc)

        about_how_title = QLabel("How to use")
        about_how_title.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #9fd3ff; margin-top: 4px;"
        )
        about_vbox.addWidget(about_how_title)

        how_to_text = QLabel(
            "1. Select your game from the dropdown.\n"
            "2. Choose <b>Cloud Storage</b> for cloud/cross-device sync, or leave it "
            "unchecked for direct LAN push/pull.\n"
            "3. Scan the network to discover nearby machines, then select a destination.\n"
            "4. Enter SSH credentials for the destination machine and test the connection.\n"
            "5. Hit <b>Push to Dest</b> to send saves, or <b>Pull from Dest</b> to receive them."
        )
        how_to_text.setStyleSheet("font-size: 10px; color: #bbb;")
        how_to_text.setWordWrap(True)
        about_vbox.addWidget(how_to_text)

        about_links_row = QHBoxLayout()

        github_btn = QPushButton("  View on GitHub")
        github_btn.setStyleSheet(
            "QPushButton { background-color: #24292e; color: white;"
            " border: 1px solid #555; padding: 4px 10px; font-size: 11px; }"
            "QPushButton:hover { background-color: #3a3f44; }"
        )
        github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_btn.clicked.connect(
            lambda: webbrowser.open("https://github.com/Perezented/Game-Sync")
        )
        about_links_row.addWidget(github_btn)

        donate_btn = QPushButton("  Donate via PayPal ♥")
        donate_btn.setStyleSheet(
            "QPushButton { background-color: #003087; color: white;"
            " border: 1px solid #0070ba; padding: 4px 10px; font-size: 11px; }"
            "QPushButton:hover { background-color: #0070ba; }"
        )
        donate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        donate_btn.clicked.connect(
            lambda: webbrowser.open("https://www.paypal.com/ncp/payment/J4WYMPBFTLBMU")
        )
        about_links_row.addWidget(donate_btn)
        about_links_row.addStretch()
        about_vbox.addLayout(about_links_row)

        about_vbox.addStretch()
        footer_tabs.addTab(about_tab, "ℹ  About")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle { background-color: #555; height: 4px; }"
        )
        splitter.addWidget(scroll_area)
        splitter.addWidget(footer_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([9999, 180])

        outer_layout.addWidget(splitter)
        self.setCentralWidget(outer_widget)

    # ── Cloud UI callbacks ────────────────────────────────────────────────────

    def toggle_cloud_section(self, enabled: bool):
        self.cloud_section.setVisible(enabled)
        if enabled:
            self._refresh_rclone_banner()
        cloud_on = enabled
        dest_selected = bool(self._current_dest_mac or self._current_dest_ip)
        self.sync_button.setVisible(not cloud_on and dest_selected)
        self.pull_dest_btn.setVisible(not cloud_on and dest_selected)
        self.push_cloud_btn.setVisible(cloud_on)
        self.pull_cloud_btn.setVisible(cloud_on)
        self.cloud_op_status_label.setVisible(cloud_on)
        self.direct_sync_status_label.setVisible(not cloud_on)
        self.sync_direction_label.setVisible(False)
        self.sync_direction_dropdown.setVisible(False)
        self.dest_machine_widget.setVisible(not cloud_on)
        self.dest_ssh_section.setVisible(not cloud_on and dest_selected)
        self.dest_label.setVisible(not cloud_on and dest_selected)
        self.dest_path.setVisible(not cloud_on and dest_selected)
        self.dest_default_btn.setVisible(not cloud_on and dest_selected)
        if cloud_on:
            self._refresh_cloud_folder_default()
        self.save_settings()

    def on_cloud_provider_changed(self, btn_id: int, checked: bool):
        if not checked:
            return
        self.gdrive_section.setVisible(btn_id in (0, 2))
        self.dropbox_section.setVisible(btn_id in (1, 2))
        self.local_machine_section.setVisible(btn_id == 3)
        is_local = btn_id == 3
        self.cloud_folder_input.setPlaceholderText(
            "/GameSync/<GameName>/"
            if not is_local
            else "(sub-folder appended to remote path above, e.g. Zomboid)"
        )
        self.cloud_folder_row.setVisible(not is_local)
        if (
            btn_id == 3
            and self.lm_host_dropdown.count() <= 1
            and not getattr(self, "scan_active", False)
        ):
            self.start_network_scan()
        self._refresh_local_machine_scan_state()
        self._refresh_rclone_banner()

    def _refresh_cloud_folder_default(self):
        """Populate cloud folder with a game-specific saved path or default value."""
        game = self.game_dropdown.currentText() or "Game"
        saved_clouds = self.previous_paths.get("game_cloud_folders", {})
        saved_folder = saved_clouds.get(game)
        if saved_folder:
            self.cloud_folder_input.setText(saved_folder)
            return

        self.cloud_folder_input.setText(f"/GameSync/{game}/")

    def _refresh_local_machine_scan_state(self):
        has_hosts = self.lm_host_dropdown.count() > 1
        self.lm_host_dropdown.setEnabled(has_hosts)
        lm_active = (
            self.cloud_enabled_checkbox.isChecked()
            and self.cloud_provider_group.checkedId() == 3
        )
        self.lm_scan_progress.setVisible(self.scan_active and lm_active)

    def _remote_os_from_direction(self, direction: str) -> str:
        if "↔" not in direction:
            return "Linux"
        left, right = [part.strip() for part in direction.split("↔")]
        return right

    def _default_game_path(self, field: str) -> str:
        game = self.game_dropdown.currentText()
        if not game:
            return ""
        defaults = self.game_defaults.get(game, {})
        if field == "source":
            key = "linux" if self.local_os == "Linux" else "windows"
        else:
            remote_os = self._remote_os_from_direction(
                self.sync_direction_dropdown.currentText()
            )
            key = "linux" if remote_os == "Linux" else "windows"
        return defaults.get(key, "")

    def _set_default_source_path(self):
        default_path = self._default_game_path("source")
        if default_path:
            self.source_path.setText(default_path)
            self.save_settings()

    def _set_default_dest_path(self):
        default_path = self._default_game_path("dest")
        if default_path:
            self.dest_path.setText(default_path)
            self.save_settings()

    # ── Custom / emulator game management ────────────────────────────────────

    def _is_custom_game(self, name: str) -> bool:
        """Return True if the named game was added by the user (not a built-in)."""
        builtin_names = {g["name"] for g in GAME_DEFAULTS}
        return name not in builtin_names

    def _add_custom_game(self):
        dlg = _CustomGameDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        name = data["name"]

        if name in self.game_defaults:
            QMessageBox.warning(
                self, "Duplicate name",
                f'A game named "{name}" already exists. '
                "Please use a different name or remove the existing entry first."
            )
            return

        # Register in the runtime defaults dict
        self.game_defaults[name] = {
            "windows": data["windows"],
            "linux": data["linux"],
            "steamdeck": data["linux"],  # use same path for SteamDeck
        }
        self.game_dropdown.addItem(name)
        self.game_dropdown.setCurrentText(name)

        # Persist
        custom_games = self.previous_paths.get("custom_games", [])
        custom_games.append(data)
        self.previous_paths["custom_games"] = custom_games
        self.save_settings()

    def _remove_custom_game(self):
        name = self.game_dropdown.currentText()
        if not name:
            return
        if not self._is_custom_game(name):
            QMessageBox.information(
                self, "Built-in game",
                f'"{name}" is a built-in game and cannot be removed.'
            )
            return
        reply = QMessageBox.question(
            self, "Remove game",
            f'Remove "{name}" from the game list?\n'
            "Saved paths for this game will also be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Remove from runtime dict and dropdown
        self.game_defaults.pop(name, None)
        idx = self.game_dropdown.findText(name)
        if idx >= 0:
            self.game_dropdown.removeItem(idx)

        # Remove from persistent custom_games list
        custom_games = self.previous_paths.get("custom_games", [])
        custom_games = [g for g in custom_games if g.get("name") != name]
        self.previous_paths["custom_games"] = custom_games
        self.save_settings()

    def _browse_folder(self, target_input: QLineEdit):
        """Open a cross-platform folder picker and write the selection to a line edit."""
        current_text = target_input.text().strip()
        start_dir = Path.home()

        if current_text:
            try:
                expanded = Path(
                    current_text
                    .replace("%USERPROFILE%", str(Path.home()))
                    .replace("%APPDATA%", str(Path.home() / "AppData" / "Roaming"))
                ).expanduser()
                candidate = expanded if expanded.is_dir() else expanded.parent
                if candidate.exists():
                    start_dir = candidate
            except Exception:
                pass

        selected_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Folder",
            str(start_dir),
        )
        if selected_dir:
            target_input.setText(selected_dir)
            self.save_settings()

    # ── Destination machine SSH helpers ───────────────────────────────────────

    def _browse_dest_ssh_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home() / ".ssh"), "All files (*)"
        )
        if path:
            self.dest_ssh_key_input.setText(path)

    def _set_lm_password(self):
        password, ok = QInputDialog.getText(
            self,
            "Local Machine SSH Password",
            "Enter SSH password for the local network machine:",
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            self.lm_password = password
            self.lm_pass_btn.setText("Password Set ✓")
            self.lm_pass_btn.setStyleSheet("color: #7ed6a9;")

    def _set_dest_password(self):
        password, ok = QInputDialog.getText(
            self,
            "Destination SSH Password",
            "Enter SSH password for the destination machine:",
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            self.dest_password = password
            self.dest_ssh_pass_btn.setText("Password Set ✓")
            self.dest_ssh_pass_btn.setStyleSheet("color: #7ed6a9;")

    def _build_dest_sync(self) -> "LocalNetworkSync | None":
        """Build a LocalNetworkSync for the currently selected destination machine."""
        ip = self._current_dest_ip
        usr = self.dest_ssh_user_input.text().strip()
        if not ip or not usr:
            return None
        port_txt = self.dest_ssh_port_input.text().strip()
        try:
            port = int(port_txt) if port_txt else 22
        except ValueError:
            port = 22
        key = self.dest_ssh_key_input.text().strip()
        password = getattr(self, "dest_password", "")
        return LocalNetworkSync(ip, usr, "/", port, key, password)

    def _test_dest_connection(self):
        usr = self.dest_ssh_user_input.text().strip()
        if not self._current_dest_ip or not usr:
            self.dest_ssh_status_label.setText(
                "Select a destination machine and enter username first."
            )
            self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return

        sync_obj = self._build_dest_sync()
        if sync_obj is None:
            return

        if not sync_obj.ssh_key and not sync_obj.ssh_password:
            password = self._set_dest_password()  # type: ignore[func-returns-value]
            sync_obj = self._build_dest_sync()
            if sync_obj is None:
                return

        self.dest_ssh_test_btn.setEnabled(False)
        self.dest_ssh_test_btn.setStyleSheet("")
        self.dest_ssh_user_input.setStyleSheet("")
        self.dest_ssh_status_label.setText("Testing…")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.dest_ssh_progress.setVisible(True)

        print(
            f"[dest_test] spawning thread: ip={sync_obj.ip!r} port={sync_obj.ssh_port} user={sync_obj.username!r} key={sync_obj.ssh_key!r} has_pw={bool(sync_obj.ssh_password)}"
        )
        self._dest_test_thread = ConnectionTestThread(sync_obj)
        self._dest_test_thread.finished.connect(self._on_dest_test_done)
        self._dest_test_thread.start()

    def _on_dest_test_done(self, ok: bool, msg: str):
        self.dest_ssh_test_btn.setEnabled(True)
        self.dest_ssh_progress.setVisible(False)
        if ok:
            self.dest_ssh_status_label.setText("✓ " + msg[:70])
            self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self.dest_ssh_test_btn.setStyleSheet("border: 2px solid #7ed6a9;")
            self.dest_ssh_user_input.setStyleSheet("")
            self.save_settings()
        else:
            self.dest_ssh_status_label.setText("✗ " + msg[:70])
            self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: red;")
            self.dest_ssh_test_btn.setStyleSheet("border: 2px solid red;")
            self.dest_ssh_user_input.setStyleSheet("border: 1px solid red;")

    # ── Direct machine-to-machine sync ────────────────────────────────────────

    def _start_direct_sync(self, operation: str):
        """Common launcher for push/pull between this machine and the destination."""
        if (
            getattr(self, "settings_confirm_sync_cb", None)
            and self.settings_confirm_sync_cb.isChecked()
        ):
            op_label = (
                "Push to destination"
                if operation == "push"
                else "Pull from destination"
            )
            reply = QMessageBox.question(
                self,
                "Confirm Sync",
                f"Are you sure you want to {op_label.lower()}?\nThis will overwrite files at the target.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        src = self.source_path.text().strip()
        dest = self.dest_path.text().strip()
        if not src or not dest:
            self.direct_sync_status_label.setText(
                "Source Path and Destination Path must both be set."
            )
            self.direct_sync_status_label.setStyleSheet(
                "font-size: 10px; color: orange;"
            )
            self.direct_sync_status_label.setVisible(True)
            return

        sync_obj = self._build_dest_sync()
        if sync_obj is None:
            self.direct_sync_status_label.setText(
                "Fill in Destination SSH Username (and credentials) first."
            )
            self.direct_sync_status_label.setStyleSheet(
                "font-size: 10px; color: orange;"
            )
            self.direct_sync_status_label.setVisible(True)
            return

        if not sync_obj.ssh_key and not sync_obj.ssh_password:
            self._set_dest_password()
            sync_obj = self._build_dest_sync()
            if not getattr(self, "dest_password", ""):
                return

        local_path = src
        remote_path = dest

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.direct_sync_status_label.setVisible(True)
        self.warning_label.setVisible(True)
        self.save_settings()
        self.sync_active = True

        self._direct_worker = DirectSyncWorkerThread(
            sync_obj, operation, local_path, remote_path
        )
        self._direct_worker.progress.connect(self._on_direct_sync_progress)
        self._direct_worker.finished.connect(self._on_direct_sync_finished)
        self._direct_worker.start()

        if operation == "push":
            self.sync_button.setEnabled(True)
            self.sync_button.setText("⏹  Cancel Push")
            self.sync_button.setStyleSheet("background-color: #8a3a3a; color: white;")
            try:
                self.sync_button.clicked.disconnect()
            except Exception:
                pass
            self.sync_button.clicked.connect(self._cancel_direct_sync)
            self.pull_dest_btn.setEnabled(False)
        else:
            self.sync_button.setVisible(True)
            self.pull_dest_btn.setVisible(True)
            self.pull_dest_btn.setEnabled(True)
            self.pull_dest_btn.setText("⏹  Cancel Pull")
            self.pull_dest_btn.setStyleSheet("background-color: #8a3a3a; color: white;")
            try:
                self.pull_dest_btn.clicked.disconnect()
            except Exception:
                pass
            self.pull_dest_btn.clicked.connect(self._cancel_direct_sync)
            self.sync_button.setEnabled(False)

    def _log_append(self, msg: str):
        """Append a line to the sync log panel."""
        self.sync_log.appendPlainText(msg)
        sb = self.sync_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_direct_sync_progress(self, msg: str):
        self.direct_sync_status_label.setText(msg[:120])
        self._log_append(msg)

    def _on_direct_sync_finished(self, ok: bool, msg: str):
        self.sync_active = False
        try:
            self.sync_button.clicked.disconnect()
        except Exception:
            pass
        self.sync_button.clicked.connect(self.start_sync)
        self.sync_button.setText("⬆  Push to Dest")
        self.sync_button.setStyleSheet("background-color: #3a5a8a; color: white;")
        self.sync_button.setEnabled(True)

        try:
            self.pull_dest_btn.clicked.disconnect()
        except Exception:
            pass
        self.pull_dest_btn.clicked.connect(self.pull_from_dest)
        self.pull_dest_btn.setText("⬇  Pull from Dest")
        self.pull_dest_btn.setStyleSheet("background-color: #3a6a4a; color: white;")
        self.pull_dest_btn.setEnabled(True)

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if ok else 0)
        QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False))
        color = "#7ed6a9" if ok else ("orange" if msg == "Cancelled." else "red")
        self.direct_sync_status_label.setText(("✓ " if ok else "✗ ") + msg)
        self.direct_sync_status_label.setStyleSheet(f"font-size: 10px; color: {color};")
        self._log_append(("✓ " if ok else "✗ ") + msg)

    def _cancel_direct_sync(self):
        worker = getattr(self, "_direct_worker", None)
        if worker and worker.isRunning():
            self._log_append("── Cancelling…")
            worker.cancel()

    def pull_from_dest(self):
        self._start_direct_sync("pull")

    # ── Google Drive auth ─────────────────────────────────────────────────────

    def _authorize_rclone(self, provider: str):
        """Run 'rclone authorize' for the given provider. Opens browser, captures token."""
        import re as _re
        import threading

        status_label = (
            self.gd_status_label if provider == "gdrive" else self.db_status_label
        )
        connect_btn = (
            self.gd_connect_btn if provider == "gdrive" else self.db_connect_btn
        )
        rclone_type = "drive" if provider == "gdrive" else "dropbox"

        if not rclone_is_available():
            status_label.setText("rclone not found — install from rclone.org")
            status_label.setStyleSheet("font-size: 10px; color: red;")
            return

        connect_btn.setEnabled(False)
        status_label.setText("Opening browser… waiting for authorization…")
        status_label.setStyleSheet("font-size: 10px; color: lightgray;")

        _provider = provider
        _rclone_type = rclone_type

        def _run():
            try:
                try:
                    if platform.system() == "Windows":
                        subprocess.run(
                            [
                                "powershell",
                                "-NoLogo",
                                "-NoProfile",
                                "-Command",
                                "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'rclone.exe' -and $_.CommandLine -match 'authorize' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }",
                            ],
                            capture_output=True,
                            text=True,
                            creationflags=_CREATE_NO_WINDOW,
                        )
                    else:
                        subprocess.run(
                            ["pkill", "-f", "rclone authorize"],
                            capture_output=True,
                        )
                except Exception:
                    pass
                import time as _time

                _time.sleep(0.5)

                result = subprocess.run(
                    [
                        "rclone",
                        "authorize",
                        _rclone_type,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=_CREATE_NO_WINDOW,
                )
                output = result.stdout + result.stderr
                if "---" + ">" in output and "<---End paste" in output:
                    token_json = (
                        output.split("---" + ">", 1)[1]
                        .split("<---End paste", 1)[0]
                        .strip()
                    )
                else:
                    m = _re.search(
                        r'(\{[^{}]*"access_token"[^{}]*\})', output, _re.DOTALL
                    )
                    if m:
                        token_json = m.group(1).strip()
                    else:
                        raise RuntimeError(
                            f"Could not parse rclone token (exit={result.returncode}).\n"
                            f"Full output:\n{output}"
                        )
                self._rclone_auth_token.emit(_provider, token_json)
            except Exception as exc:
                self._rclone_auth_err.emit(_provider, str(exc)[:2000])

        threading.Thread(target=_run, daemon=True).start()

    def _apply_rclone_token(self, provider: str, token_json: str):
        """Called on main thread via signal after successful rclone authorize."""
        if provider == "gdrive":
            self.rclone_gdrive = RcloneSync("gdrive", token_json)
            self._store_rclone_token("gdrive", token_json)
        else:
            self.rclone_dropbox = RcloneSync("dropbox", token_json)
            self._store_rclone_token("dropbox", token_json)
        self.save_settings()
        self._rclone_auth_ok.emit(provider)

    def _on_rclone_authorized(self, provider: str):
        status_label = (
            self.gd_status_label if provider == "gdrive" else self.db_status_label
        )
        connect_btn = (
            self.gd_connect_btn if provider == "gdrive" else self.db_connect_btn
        )
        logout_btn = self.gd_logout_btn if provider == "gdrive" else self.db_logout_btn
        connect_btn.setEnabled(True)
        connect_btn.setText("Re-authorize")
        logout_btn.setVisible(True)
        status_label.setText("✓ Authorized")
        status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")

    def _logout_rclone(self, provider: str):
        """Clear stored token and reset auth state for the given provider."""
        if provider == "gdrive":
            self.rclone_gdrive = None
            self._delete_rclone_token("gdrive")
            status_label = self.gd_status_label
            connect_btn = self.gd_connect_btn
            logout_btn = self.gd_logout_btn
        else:
            self.rclone_dropbox = None
            self._delete_rclone_token("dropbox")
            status_label = self.db_status_label
            connect_btn = self.db_connect_btn
            logout_btn = self.db_logout_btn
        cfg_path = (
            Path.home() / ".config" / "game-sync-tool" / f"rclone_{provider}.conf"
        )
        try:
            cfg_path.unlink(missing_ok=True)
        except Exception:
            pass
        self.save_settings()
        logout_btn.setVisible(False)
        connect_btn.setText(
            "Authorize Google Drive" if provider == "gdrive" else "Authorize Dropbox"
        )
        status_label.setText("Not authorized")
        status_label.setStyleSheet("font-size: 10px; color: gray;")

    def _refresh_rclone_banner(self):
        """Show the rclone-not-found banner only when relevant providers are selected."""
        rclone_missing = not rclone_is_available()
        btn_id = self.cloud_provider_group.checkedId()
        needs_rclone = btn_id in (0, 1, 2)
        self.rclone_banner.setVisible(rclone_missing and needs_rclone)

    def _on_rclone_auth_error(self, provider: str, msg: str):
        status_label = (
            self.gd_status_label if provider == "gdrive" else self.db_status_label
        )
        connect_btn = (
            self.gd_connect_btn if provider == "gdrive" else self.db_connect_btn
        )
        connect_btn.setEnabled(True)
        short = msg[:80] + ("…" if len(msg) > 80 else "")
        status_label.setText(f"Auth error — {short}")
        status_label.setStyleSheet("font-size: 10px; color: red;")
        err_box = QMessageBox(self)
        err_box.setWindowTitle("rclone Authorization Error")
        err_box.setIcon(QMessageBox.Icon.Critical)
        err_box.setText("rclone authorization failed.")
        err_box.setDetailedText(msg)
        err_box.exec()
        self._log_append(f"[rclone auth error — {provider}]\n{msg}")

    # ── Cloud push / pull ─────────────────────────────────────────────────────

    def _active_cloud_sync_objects(self) -> list:
        """Return whichever cloud sync objects are ready based on selected provider."""
        btn_id = self.cloud_provider_group.checkedId()
        objects = []
        if (
            btn_id in (0, 2)
            and self.rclone_gdrive
            and self.rclone_gdrive.is_authenticated()
        ):
            objects.append(("Google Drive", self.rclone_gdrive))
        if (
            btn_id in (1, 2)
            and self.rclone_dropbox
            and self.rclone_dropbox.is_authenticated()
        ):
            objects.append(("Dropbox", self.rclone_dropbox))
        if (
            btn_id == 3
            and self.local_network_sync
            and self.local_network_sync.is_authenticated()
        ):
            objects.append(("Local Machine", self.local_network_sync))
        return objects

    def _cloud_folder_for_game(self) -> str:
        game = self.game_dropdown.currentText() or "Game"
        if self.cloud_provider_group.checkedId() == 3:
            return game
        folder = self.cloud_folder_input.text().strip()
        if not folder:
            folder = f"/GameSync/{game}/"
        return folder

    def push_to_cloud(self):
        if (
            getattr(self, "settings_confirm_sync_cb", None)
            and self.settings_confirm_sync_cb.isChecked()
        ):
            reply = QMessageBox.question(
                self,
                "Confirm Push",
                "Are you sure you want to push saves to the cloud?\nThis will overwrite files in the cloud folder.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        local_path = self.source_path.text().strip()
        if not local_path:
            self.cloud_op_status_label.setText("Set a Source Path first.")
            return
        cloud_syncs = self._active_cloud_sync_objects()
        if not cloud_syncs:
            self.cloud_op_status_label.setText(
                "No authenticated cloud provider available."
            )
            return
        self._run_cloud_op("upload", cloud_syncs, local_path)

    def pull_from_cloud(self):
        if (
            getattr(self, "settings_confirm_sync_cb", None)
            and self.settings_confirm_sync_cb.isChecked()
        ):
            reply = QMessageBox.question(
                self,
                "Confirm Pull",
                "Are you sure you want to pull saves from the cloud?\nThis will overwrite local files.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        local_path = self.source_path.text().strip()
        if not local_path:
            self.cloud_op_status_label.setText("Set a Source Path first.")
            return
        cloud_syncs = self._active_cloud_sync_objects()
        if not cloud_syncs:
            self.cloud_op_status_label.setText(
                "No authenticated cloud provider available."
            )
            return
        self._run_cloud_op("download", cloud_syncs, local_path)

    def _run_cloud_op(self, operation: str, cloud_syncs: list, local_path: str):
        self.sync_active = True
        cloud_folder = self._cloud_folder_for_game()
        name, sync_obj = cloud_syncs[0]

        self.push_cloud_btn.setEnabled(False)
        self.pull_cloud_btn.setEnabled(False)
        self.cloud_op_status_label.setText(f"{operation.title()}ing via {name}…")
        self.progress_bar.setVisible(True)
        self.warning_label.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.cloud_worker = CloudWorkerThread(
            operation, sync_obj, local_path, cloud_folder
        )
        self.cloud_worker.progress.connect(self.cloud_op_status_label.setText)
        self.cloud_worker.progress.connect(self._log_append)
        self.cloud_worker.finished.connect(
            lambda ok, msg: self._on_cloud_op_finished(
                ok, msg, cloud_syncs[1:], operation, local_path, cloud_folder
            )
        )
        self.cloud_worker.start()

        if operation == "upload":
            self.push_cloud_btn.setEnabled(True)
            self.push_cloud_btn.setText("⏹  Cancel Push")
            self.push_cloud_btn.setStyleSheet(
                "background-color: #8a3a3a; color: white;"
            )
            try:
                self.push_cloud_btn.clicked.disconnect()
            except Exception:
                pass
            self.push_cloud_btn.clicked.connect(self._cancel_cloud_sync)
            self.pull_cloud_btn.setEnabled(False)
        else:
            self.pull_cloud_btn.setEnabled(True)
            self.pull_cloud_btn.setText("⏹  Cancel Pull")
            self.pull_cloud_btn.setStyleSheet(
                "background-color: #8a3a3a; color: white;"
            )
            try:
                self.pull_cloud_btn.clicked.disconnect()
            except Exception:
                pass
            self.pull_cloud_btn.clicked.connect(self._cancel_cloud_sync)
            self.push_cloud_btn.setEnabled(False)

    def _on_cloud_op_finished(
        self,
        ok: bool,
        msg: str,
        remaining: list,
        operation: str,
        local_path: str,
        cloud_folder: str,
    ):
        cancelled = getattr(self, "_cloud_cancelled", False)
        if not ok or cancelled:
            if cancelled or msg == "Cancelled.":
                label = "Cancelled."
                color = "orange"
                self._log_append("⏹ Cancelled.")
            else:
                label = self._friendly_cloud_error(msg)
                color = "red"
                self._log_append(f"✗ {msg}")
                err_box = QMessageBox(self)
                err_box.setWindowTitle("Cloud Sync Failed")
                err_box.setIcon(QMessageBox.Icon.Warning)
                err_box.setText(label)
                err_box.setDetailedText(msg)
                err_box.exec()
            self.cloud_op_status_label.setText(label)
            self.cloud_op_status_label.setStyleSheet(
                f"font-size: 10px; color: {color};"
            )
            self._reset_cloud_buttons()
            return

        if remaining:
            name, sync_obj = remaining[0]
            self.cloud_op_status_label.setText(f"{operation.title()}ing via {name}…")
            self.cloud_worker = CloudWorkerThread(
                operation, sync_obj, local_path, cloud_folder
            )
            self.cloud_worker.progress.connect(self.cloud_op_status_label.setText)
            self.cloud_worker.progress.connect(self._log_append)
            self.cloud_worker.finished.connect(
                lambda ok2, msg2: self._on_cloud_op_finished(
                    ok2, msg2, remaining[1:], operation, local_path, cloud_folder
                )
            )
            self.cloud_worker.start()
        else:
            self._log_append(f"✓ {msg}")
            self.cloud_op_status_label.setText(f"✓ {msg}")
            self.cloud_op_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self._reset_cloud_buttons()

    def _friendly_cloud_error(self, msg: str) -> str:
        """Translate a raw rclone/sync error into a short, user-readable sentence."""
        m = msg.lower()
        if "directory not found" in m or "not found" in m:
            return (
                "Cloud folder not found — push your saves first before pulling, "
                "or check that the Cloud Folder path matches what was uploaded."
            )
        if "invalid_grant" in m or "token has been expired" in m or "oauth" in m:
            return "Cloud authorization expired — re-authorize in the Cloud Sync section."
        if "permission denied" in m or "errno 13" in m:
            return "Permission denied — check that the local save path is writable."
        if "no space left" in m:
            return "Not enough disk space to complete the download."
        if "connection refused" in m or "network" in m or "dial tcp" in m:
            return "Network error — check your internet connection and try again."
        if "rclone exited with code" in m:
            code = m.split("code")[-1].strip().split()[0]
            return f"rclone failed (exit code {code}) — see the Sync Log for details."
        return f"Sync failed — {msg[:120]}"

    def _cancel_cloud_sync(self):
        self._cloud_cancelled = True
        worker = self.cloud_worker
        if worker and worker.isRunning():
            self._log_append("── Cancelling cloud sync…")
            worker.cancel()

    def _reset_cloud_buttons(self):
        self.sync_active = False
        self._cloud_cancelled = False
        try:
            self.push_cloud_btn.clicked.disconnect()
        except Exception:
            pass
        self.push_cloud_btn.clicked.connect(self.push_to_cloud)
        self.push_cloud_btn.setText("⬆  Push to Cloud")
        self.push_cloud_btn.setStyleSheet("background-color: #2a5f8a; color: white;")
        self.push_cloud_btn.setEnabled(True)
        try:
            self.pull_cloud_btn.clicked.disconnect()
        except Exception:
            pass
        self.pull_cloud_btn.clicked.connect(self.pull_from_cloud)
        self.pull_cloud_btn.setText("⬇  Pull from Cloud")
        self.pull_cloud_btn.setStyleSheet("background-color: #2a6b4a; color: white;")
        self.pull_cloud_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.warning_label.setVisible(False)

    # ── Local network machine helpers ─────────────────────────────────────────

    def populate_local_cloud_dropdown(self):
        """Re-fill the local machine dropdown from whatever was last scanned."""
        current_idx = self.lm_host_dropdown.currentIndex()
        current_text = self.lm_host_dropdown.currentText()

        self.lm_host_dropdown.blockSignals(True)
        self.lm_host_dropdown.clear()
        self.lm_host_dropdown.addItem("— select from scanned machines —")

        for ip, os_type, label, mac, is_local in self.scanned_hosts:
            if not is_local:
                self.lm_host_dropdown.addItem(label)

        saved_ip = self.previous_paths.get("lm_ip", "")
        if saved_ip:
            for i in range(self.lm_host_dropdown.count()):
                if saved_ip in self.lm_host_dropdown.itemText(i):
                    self.lm_host_dropdown.setCurrentIndex(i)
                    break
        elif current_text and current_text != "— select from scanned machines —":
            idx = self.lm_host_dropdown.findText(current_text)
            if idx >= 0:
                self.lm_host_dropdown.setCurrentIndex(idx)

        self.lm_host_dropdown.blockSignals(False)
        self._refresh_local_machine_scan_state()

    def _on_lm_host_selected(self, index: int):
        if index <= 0:
            return
        label = self.lm_host_dropdown.currentText()
        for ip, os_type, disp_label, mac, is_local in self.scanned_hosts:
            if disp_label == label:
                self.previous_paths["lm_ip"] = ip
                if not self.lm_username_input.text():
                    self.lm_username_input.setPlaceholderText(
                        "user" if os_type == "Linux" else "windows_user_login"
                    )
                break
        self._build_local_network_sync()

    def _browse_ssh_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home() / ".ssh"), "All files (*)"
        )
        if path:
            self.lm_ssh_key_input.setText(path)

    # ── Settings-tab helpers ──────────────────────────────────────────────────

    def _st_browse_ssh_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home() / ".ssh"), "All files (*)"
        )
        if path:
            self.st_lm_ssh_key_input.setText(path)
            self._st_sync_to_main()

    def _st_sync_to_main(self):
        """Copy settings-tab mirror fields → main cloud section fields, then save."""
        self.lm_username_input.setText(self.st_lm_username_input.text())
        self.lm_remote_path_input.setText(self.st_lm_remote_path_input.text())
        self.lm_port_input.setText(self.st_lm_port_input.text() or "22")
        self.lm_ssh_key_input.setText(self.st_lm_ssh_key_input.text())
        self.save_settings()

    def _st_clear_saved_paths(self):
        reply = QMessageBox.question(
            self,
            "Clear saved paths?",
            "This will erase all remembered source/destination paths for every "
            "game+machine combination.\n\n"
            "Your credentials, cloud tokens and other settings are NOT affected.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.previous_paths.pop("game_machine_paths", None)
        self.save_settings()
        self._st_reload_json()
        self.st_json_status.setText("✓ Saved paths cleared.")
        self.st_json_status.setStyleSheet("font-size: 10px; color: #7ed6a9;")

    def _st_reload_json(self):
        """Load the settings file from disk and display it in the JSON editor."""
        try:
            if self.settings_file.exists():
                raw = self.settings_file.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
                self.settings_json_editor.setPlainText(pretty)
                self.st_json_status.setText("Loaded from disk.")
                self.st_json_status.setStyleSheet("font-size: 10px; color: gray;")
            else:
                self.settings_json_editor.setPlainText("{}")
                self.st_json_status.setText("No settings file found yet.")
                self.st_json_status.setStyleSheet("font-size: 10px; color: gray;")
        except Exception as exc:
            self.st_json_status.setText(f"Error: {exc}")
            self.st_json_status.setStyleSheet("font-size: 10px; color: red;")

    def _st_save_json(self):
        """Validate and write the JSON editor contents to disk, then reload UI."""
        raw = self.settings_json_editor.toPlainText().strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.st_json_status.setText(f"❌ Invalid JSON – {exc}")
            self.st_json_status.setStyleSheet("font-size: 10px; color: red;")
            return
        try:
            self._write_settings_file(parsed)
            self.previous_paths = parsed
            self.st_json_status.setText("✓ Saved successfully.")
            self.st_json_status.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self.settings_json_editor.setPlainText(
                json.dumps(parsed, indent=2, ensure_ascii=False)
            )
        except Exception as exc:
            self.st_json_status.setText(f"Write error: {exc}")
            self.st_json_status.setStyleSheet("font-size: 10px; color: red;")

    def _request_local_machine_password(self) -> str | None:
        password, ok = QInputDialog.getText(
            self,
            "SSH Password",
            "Enter the SSH password for the selected local machine:",
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            self.lm_password = password
            return password
        return None

    def _build_local_network_sync(self) -> bool:
        """Create a LocalNetworkSync from the current UI fields. Returns True if valid."""
        ip = self.previous_paths.get("lm_ip", "")
        username = self.lm_username_input.text().strip()
        rpath = self.lm_remote_path_input.text().strip()
        port_txt = self.lm_port_input.text().strip()
        key = self.lm_ssh_key_input.text().strip()

        if not ip:
            label = self.lm_host_dropdown.currentText()
            for ip2, _, disp, _, _ in self.scanned_hosts:
                if disp == label:
                    ip = ip2
                    break

        try:
            port = int(port_txt) if port_txt else 22
        except ValueError:
            port = 22

        if not ip or not username or not rpath:
            return False

        password = getattr(self, "lm_password", "")
        self.local_network_sync = LocalNetworkSync(
            ip, username, rpath, port, key, password
        )
        return True

    def _test_local_machine_connection(self):
        if not self._build_local_network_sync():
            self.lm_status_label.setText(
                "Fill in Machine, Username, and Remote Path first."
            )
            self.lm_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return

        if (
            not self.local_network_sync.ssh_key
            and not self.local_network_sync.ssh_password
        ):
            password = self._request_local_machine_password()
            if not password:
                self.lm_status_label.setText(
                    "SSH password required or provide an SSH key."
                )
                self.lm_status_label.setStyleSheet("font-size: 10px; color: orange;")
                return
            self.local_network_sync.ssh_password = password

        self.lm_test_btn.setEnabled(False)
        self.lm_test_btn.setStyleSheet("")
        self.lm_status_label.setText("Testing…")
        self.lm_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.lm_scan_progress.setVisible(True)

        obj = self.local_network_sync
        print(
            f"[lm_test] spawning thread: ip={obj.ip!r} port={obj.ssh_port} user={obj.username!r} key={obj.ssh_key!r} has_pw={bool(obj.ssh_password)}"
        )
        self._lm_test_thread = ConnectionTestThread(obj)
        self._lm_test_thread.finished.connect(self._on_lm_test_done)
        self._lm_test_thread.start()

    def _on_lm_test_done(self, ok: bool, msg: str):
        self.lm_test_btn.setEnabled(True)
        self.lm_scan_progress.setVisible(False)
        if ok:
            self.lm_status_label.setText("✓ " + msg[:70])
            self.lm_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self.lm_test_btn.setStyleSheet("border: 2px solid #7ed6a9;")
            self.save_settings()
        else:
            self.lm_status_label.setText("✗ " + msg[:70])
            self.lm_status_label.setStyleSheet("font-size: 10px; color: red;")
            self.lm_test_btn.setStyleSheet("border: 2px solid red;")

    # ── Network scan ──────────────────────────────────────────────────────────

    def on_scan_timer_timeout(self):
        if self.sync_active or not self._should_auto_scan_network():
            return
        self.start_network_scan()

    def _should_auto_scan_network(self) -> bool:
        if not getattr(self, "settings_auto_scan_cb", None):
            return False
        if not self.settings_auto_scan_cb.isChecked():
            return False
        if self._current_dest_mac:
            return False
        if self.scan_dropdown.count() <= 1 and not self.scan_performed:
            return True
        return False

    def _update_scan_button_label(self):
        if self.scan_dropdown.currentIndex() > 0:
            self.scan_button.setText("Rescan Network")
        else:
            self.scan_button.setText("Scan Network")

    def start_network_scan(self):
        if self.scan_active:
            return

        self.scan_performed = True
        self.scan_active = True
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning...")
        self._refresh_local_machine_scan_state()
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
        prepared_hosts = []
        seen_hosts = set()
        self.scan_dropdown.clear()
        self.scan_dropdown.addItem("— select a destination machine —")

        for ip, os_type, label, mac in hosts:
            normalized_mac = (mac or "").lower()
            host_key = (ip, normalized_mac)
            if host_key in seen_hosts:
                continue

            is_local = self._is_local_machine(ip, normalized_mac)
            display_label = f"{label} (this machine)" if is_local else label
            prepared_hosts.append(
                (ip, os_type, display_label, normalized_mac, is_local)
            )
            seen_hosts.add(host_key)

        for interface in self.local_interfaces:
            ip = interface["ip"]
            mac = (interface["mac"] or "").lower()
            host_key = (ip, mac)
            if host_key in seen_hosts:
                continue

            hostname = socket.gethostname()
            iface_name = interface["iface"]
            display_label = (
                f"{ip}  ({hostname} / {iface_name})  [{self.local_os}] (this machine)"
            )
            prepared_hosts.append((ip, self.local_os, display_label, mac, True))
            seen_hosts.add(host_key)

        self.scanned_hosts = prepared_hosts

        last_dest_mac = self.previous_paths.get("last_dest_mac", "").lower()
        last_dest_ip = self.previous_paths.get("last_dest_ip", "")
        auto_select_index = 0

        import re
        from collections import defaultdict
        from PyQt6.QtGui import QFont

        def _extract_hostname(label: str) -> str:
            """Pull the hostname out of labels like 'IP  (hostname)  [OS]'.
            Returns the IP itself as a fallback if no parenthetical is found."""
            m = re.search(r"\(([^)]+)\)", label)
            if m:
                return m.group(1).split("/")[0].strip().lower()
            return label.split()[0]

        hostname_to_indices: dict[str, list[int]] = defaultdict(list)
        for i, (_ip, _os, _lbl, _mac, _local) in enumerate(prepared_hosts):
            key = _extract_hostname(_lbl)
            hostname_to_indices[key].append(i)

        def _add_host_item(host_idx: int, indent: bool = False) -> None:
            nonlocal auto_select_index
            h_ip, h_os, h_label, h_mac, h_local = prepared_hosts[host_idx]
            display = ("    " + h_label) if indent else h_label
            self.scan_dropdown.addItem(display)
            di = self.scan_dropdown.count() - 1
            self.scan_dropdown.setItemData(di, host_idx)
            if h_local:
                self.scan_dropdown.setItemData(
                    di, QColor("orange"), Qt.ItemDataRole.ForegroundRole
                )
            elif auto_select_index == 0:
                if (last_dest_mac and h_mac and h_mac == last_dest_mac) or (
                    last_dest_ip and h_ip == last_dest_ip
                ):
                    auto_select_index = di

        for hostname_key, indices in hostname_to_indices.items():
            if len(indices) > 1:
                first_label = prepared_hosts[indices[0]][2]
                m = re.search(r"\(([^)]+)\)", first_label)
                display_name = m.group(1).split("/")[0].strip() if m else hostname_key
                self.scan_dropdown.addItem(
                    f"▸  {display_name}  [{len(indices)} interfaces]"
                )
                hdr_di = self.scan_dropdown.count() - 1
                self.scan_dropdown.setItemData(hdr_di, -1)
                hdr_item = self.scan_dropdown.model().item(hdr_di)
                hdr_item.setFlags(Qt.ItemFlag.NoItemFlags)
                hdr_item.setForeground(QColor("#aaaaaa"))
                hdr_font = QFont(hdr_item.font())
                hdr_font.setItalic(True)
                hdr_item.setFont(hdr_font)
                for host_idx in indices:
                    _add_host_item(host_idx, indent=True)
            else:
                _add_host_item(indices[0], indent=False)

        self.scan_button.setEnabled(True)
        self._update_scan_button_label()
        self.scan_progress.setVisible(False)
        self.scan_dropdown.setEnabled(self.scan_dropdown.count() > 1)

        self.populate_local_cloud_dropdown()
        self._refresh_local_machine_scan_state()

        if auto_select_index > 0:
            self.scan_dropdown.setCurrentIndex(auto_select_index)
            self.scan_status_label.setText(
                self.scan_status_label.text() + "  (last destination auto-selected)"
            )

    def on_destination_selected(self, index):
        """Auto-set sync direction and paths when a scanned machine is selected."""
        if index <= 0:
            self._current_dest_mac = ""
            self._current_dest_ip = ""
            self.dest_ssh_section.setVisible(False)
            self.pull_dest_btn.setVisible(False)
            self.sync_button.setVisible(False)
            self.direct_sync_status_label.setVisible(False)
            self.dest_label.setVisible(False)
            self.dest_path.setVisible(False)
            self.dest_default_btn.setVisible(False)
            self._update_scan_button_label()
            return

        host_idx = self.scan_dropdown.itemData(index)
        if (
            host_idx is None
            or not isinstance(host_idx, int)
            or host_idx < 0
            or host_idx >= len(self.scanned_hosts)
        ):
            self.scan_dropdown.setCurrentIndex(0)
            return

        dest_ip, remote_os, _label, dest_mac, is_local = self.scanned_hosts[host_idx]
        if is_local:
            self.scan_status_label.setText(
                "This entry is the current machine. Choose another destination."
            )
            self._update_scan_button_label()
            return

        self._current_dest_mac = dest_mac
        self._current_dest_ip = dest_ip

        cloud_on = self.cloud_enabled_checkbox.isChecked()
        self.dest_ssh_section.setVisible(not cloud_on)
        self.pull_dest_btn.setVisible(not cloud_on)
        self.sync_button.setVisible(not cloud_on)
        self.direct_sync_status_label.setVisible(not cloud_on)
        self.dest_label.setVisible(not cloud_on)
        self.dest_path.setVisible(not cloud_on)
        self.dest_default_btn.setVisible(not cloud_on)

        saved_creds = self.previous_paths.get("dest_machine_creds", {}).get(
            dest_mac, {}
        )
        if saved_creds.get("username"):
            self.dest_ssh_user_input.setText(saved_creds["username"])
        elif not self.dest_ssh_user_input.text():
            self.dest_ssh_user_input.setPlaceholderText(
                "username / user" if remote_os == "Linux" else "Administrator"
            )
        if saved_creds.get("ssh_key"):
            self.dest_ssh_key_input.setText(saved_creds["ssh_key"])
        if saved_creds.get("port"):
            self.dest_ssh_port_input.setText(str(saved_creds["port"]))
        self.dest_password = ""
        self.dest_ssh_pass_btn.setText("Set Password")
        self.dest_ssh_pass_btn.setStyleSheet("")
        self.dest_ssh_test_btn.setStyleSheet("")
        self.dest_ssh_user_input.setStyleSheet("")
        self.dest_ssh_status_label.setText("Not tested")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: gray;")

        self.previous_paths["last_dest_mac"] = dest_mac
        self.previous_paths["last_dest_ip"] = dest_ip

        self._set_sync_direction(self.local_os, remote_os)
        self.update_paths()
        self.save_settings()
        self._update_scan_button_label()

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
        for game in GAME_DEFAULTS:
            self.game_defaults[game["name"]] = game["defaults"]
        self.game_dropdown.addItems(self.game_defaults.keys())

    def _load_custom_games_from_settings(self):
        """Append any user-saved custom games to the game list (called after load_settings)."""
        for entry in self.previous_paths.get("custom_games", []):
            name = entry.get("name", "").strip()
            if not name or name in self.game_defaults:
                continue  # skip duplicates / malformed
            self.game_defaults[name] = {
                "windows": entry.get("windows", ""),
                "linux": entry.get("linux", ""),
                "steamdeck": entry.get("linux", ""),
            }
            self.game_dropdown.addItem(name)

    def load_settings(self):
        if not self.settings_file.exists():
            return
        self._loading = True
        try:
            self._ensure_secure_settings_permissions()
            with open(self.settings_file, "r", encoding="utf-8") as f:
                self.previous_paths = json.load(f)
                game = self.previous_paths.get("game")
                if game:
                    self.game_dropdown.setCurrentText(game)
                sync_direction = self.previous_paths.get("sync_direction")
                if sync_direction:
                    self.sync_direction_dropdown.setCurrentText(sync_direction)

                key = f"{game or ''}__"
                saved = self.previous_paths.get("game_machine_paths", {}).get(key, {})
                self.source_path.setText(
                    saved.get("source_path", self.previous_paths.get("source_path", ""))
                )
                self.dest_path.setText(
                    saved.get("dest_path", self.previous_paths.get("dest_path", ""))
                )

                cloud_enabled = self.previous_paths.get("cloud_enabled", False)
                self.cloud_enabled_checkbox.setChecked(cloud_enabled)

                provider_idx = self.previous_paths.get("cloud_provider_idx", 0)
                btn = self.cloud_provider_group.button(provider_idx)
                if btn:
                    btn.setChecked(True)

                self.cloud_folder_input.setText(
                    self.previous_paths.get("cloud_folder", "")
                )
                self._refresh_cloud_folder_default()
                self._last_game_selected = self.game_dropdown.currentText()

                gdrive_token = self._retrieve_rclone_token("gdrive")
                if gdrive_token:
                    self.rclone_gdrive = RcloneSync("gdrive", gdrive_token)
                    self.gd_status_label.setText("✓ Authorized")
                    self.gd_status_label.setStyleSheet(
                        "font-size: 10px; color: #7ed6a9;"
                    )
                    self.gd_logout_btn.setVisible(True)
                    self.gd_connect_btn.setText("Re-authorize")

                dropbox_token = self._retrieve_rclone_token("dropbox")
                if dropbox_token:
                    self.rclone_dropbox = RcloneSync("dropbox", dropbox_token)
                    self.db_status_label.setText("✓ Authorized")
                    self.db_status_label.setStyleSheet(
                        "font-size: 10px; color: #7ed6a9;"
                    )
                    self.db_logout_btn.setVisible(True)
                    self.db_connect_btn.setText("Re-authorize")

                self.settings_confirm_sync_cb.setChecked(
                    self.previous_paths.get("settings_confirm_sync", False)
                )
                self.settings_auto_scan_cb.setChecked(
                    self.previous_paths.get("auto_scan_on_startup", False)
                )

                self.lm_username_input.setText(
                    self.previous_paths.get("lm_username", "")
                )
                self.lm_remote_path_input.setText(
                    self.previous_paths.get("lm_remote_path", "")
                )
                self.lm_port_input.setText(self.previous_paths.get("lm_port", "22"))
                self.lm_ssh_key_input.setText(self.previous_paths.get("lm_ssh_key", ""))
                if self.previous_paths.get("lm_ip") and self.previous_paths.get(
                    "lm_username"
                ):
                    self.lm_status_label.setText(
                        f"Saved: {self.previous_paths['lm_ip']} "
                        f"({self.previous_paths['lm_username']})"
                    )
                    self.lm_status_label.setStyleSheet(
                        "font-size: 10px; color: lightgray;"
                    )

        except Exception as err:
            print(f"Could not load settings: {err}")
        finally:
            self._loading = False

        if not self.dest_path.text():
            self.update_paths()

        # Load any custom games that were persisted in previous sessions
        self._load_custom_games_from_settings()

        if hasattr(self, "settings_json_editor"):
            self._st_reload_json()

    def _game_machine_key(self) -> str:
        """Unique key for the current (game, destination-MAC) combination."""
        game = self.game_dropdown.currentText() or "__unknown__"
        return f"{game}__{self._current_dest_mac}"

    def _on_game_or_direction_changed(self):
        """Called when the game or sync-direction dropdown changes."""
        if getattr(self, "_loading", False):
            return
        current_game = self.game_dropdown.currentText()
        if current_game != getattr(self, "_last_game_selected", ""):
            self._refresh_cloud_folder_default()
            self._last_game_selected = current_game
        self.update_paths()
        self.save_settings()

    def update_paths(self):
        selected_game = self.game_dropdown.currentText()
        selected_direction = self.sync_direction_dropdown.currentText()

        if not selected_game:
            return

        defaults = self.game_defaults.get(selected_game, {})
        if selected_direction == "Linux ↔ Linux":
            src = defaults.get("linux", "")
            dst = defaults.get("linux", "")
        elif selected_direction == "Linux ↔ Windows":
            src = defaults.get("linux", "")
            dst = defaults.get("windows", "")
        elif selected_direction == "Windows ↔ Linux":
            src = defaults.get("windows", "")
            dst = defaults.get("linux", "")
        elif selected_direction == "Windows ↔ Windows":
            src = defaults.get("windows", "")
            dst = defaults.get("windows", "")
        else:
            src = dst = ""

        key = self._game_machine_key()
        saved = self.previous_paths.get("game_machine_paths", {}).get(key, {})
        src = saved.get("source_path") or src
        dst = saved.get("dest_path") or dst

        self.source_path.setText(src)
        self.dest_path.setText(dst)

    def save_settings(self):
        if getattr(self, "_loading", False):
            return

        settings = dict(self.previous_paths)

        settings["game"] = self.game_dropdown.currentText()
        settings["sync_direction"] = self.sync_direction_dropdown.currentText()

        key = self._game_machine_key()
        game_machine_paths = settings.get("game_machine_paths", {})
        game_machine_paths[key] = {
            "source_path": self.source_path.text(),
            "dest_path": self.dest_path.text(),
            "sync_direction": self.sync_direction_dropdown.currentText(),
        }
        settings["game_machine_paths"] = game_machine_paths

        game_cloud_folders = settings.get("game_cloud_folders", {})
        game_cloud_folders[self.game_dropdown.currentText() or "__unknown__"] = (
            self.cloud_folder_input.text()
        )
        settings["game_cloud_folders"] = game_cloud_folders

        if self._current_dest_mac:
            dest_machine_creds = settings.get("dest_machine_creds", {})
            dest_machine_creds[self._current_dest_mac] = {
                "username": self.dest_ssh_user_input.text(),
                "ssh_key": self.dest_ssh_key_input.text(),
                "port": self.dest_ssh_port_input.text(),
            }
            settings["dest_machine_creds"] = dest_machine_creds

        if self._current_dest_mac:
            settings["last_dest_mac"] = self._current_dest_mac
            settings["last_dest_ip"] = self._current_dest_ip

        settings["settings_confirm_sync"] = self.settings_confirm_sync_cb.isChecked()
        settings["auto_scan_on_startup"] = self.settings_auto_scan_cb.isChecked()

        settings["cloud_enabled"] = self.cloud_enabled_checkbox.isChecked()
        settings["cloud_provider_idx"] = self.cloud_provider_group.checkedId()
        settings["cloud_folder"] = self.cloud_folder_input.text()
        settings["rclone_gdrive_token"] = self.previous_paths.get(
            "rclone_gdrive_token", ""
        )
        settings["rclone_gdrive_token_id"] = self.previous_paths.get(
            "rclone_gdrive_token_id", ""
        )
        settings["rclone_dropbox_token"] = self.previous_paths.get(
            "rclone_dropbox_token", ""
        )
        settings["rclone_dropbox_token_id"] = self.previous_paths.get(
            "rclone_dropbox_token_id", ""
        )
        settings["lm_ip"] = self.previous_paths.get("lm_ip", "")
        settings["lm_username"] = self.lm_username_input.text()
        settings["lm_remote_path"] = self.lm_remote_path_input.text()
        settings["lm_port"] = self.lm_port_input.text()
        settings["lm_ssh_key"] = self.lm_ssh_key_input.text()

        try:
            self._write_settings_file(settings)
            self.previous_paths = settings
            if hasattr(self, "settings_json_editor"):
                self.settings_json_editor.setPlainText(
                    json.dumps(settings, indent=2, ensure_ascii=False)
                )
        except Exception as err:
            print(f"Could not save settings: {err}")

    def start_sync(self):
        self._start_direct_sync("push")
