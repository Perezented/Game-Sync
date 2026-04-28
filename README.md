# Game Sync App

A PyQt6 GUI utility for keeping game save files in sync across machines and platforms. Originally built for Project Zomboid, it supports any game without native Steam Cloud Saves and can be extended to additional titles.

## Features

- **LAN machine sync** — scans the local network for reachable machines, selects a destination by MAC address, and syncs saves directly over SSH/rsync
- **Cloud sync** — push/pull saves to Google Drive or Dropbox via [rclone](https://rclone.org/) (no developer accounts required)
- **Cross-platform path handling** — supports Linux `~/` paths and Windows `%USERPROFILE%` paths with automatic expansion
- **Per-game, per-machine settings** — source/destination paths and sync direction are remembered per game per machine (keyed by MAC address)
- **Persistent settings** — all configuration is saved to `~/game_sync_settings.json` and restored on next launch, including the last-used destination machine
- **SSH credential storage** — username, SSH key, and port saved per destination machine; optional password entry via secure dialog
- **Sync direction control** — choose between Linux↔Linux, Linux↔Windows, Windows↔Linux, or Windows↔Windows
- **Dark theme UI** — scrollable settings panel with grouped sections for cloud and LAN configuration

## Contents

- `gui_app.py` — PyQt6 GUI application
- `sync_engine.py` — standalone path expansion and file copy logic
- `config.json` — example sync entries
- `game_defaults.json` — default save paths per game and platform
- `mock_data/` — example save folder structure for development and testing

## Requirements

- Python 3.10+
- PyQt6

### Optional

- [rclone](https://rclone.org/install/) — required for Google Drive and Dropbox cloud sync
- `paramiko` — required for SSH-based LAN sync
- `sshpass` — required for password-based SSH authentication (Linux package)

## Installation

1. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install Python dependencies:

```bash
pip install PyQt6 paramiko
```

3. (Optional) Install rclone for cloud sync:

```bash
# Linux/macOS — see https://rclone.org/install/
sudo -v ; curl https://rclone.org/install.sh | sudo bash
```

## Running the App

```bash
python gui_app.py
```

## Sync Engine (CLI)

The sync engine can be used standalone:

```bash
python sync_engine.py "<source>" "<destination>"
```

Example:

```bash
python sync_engine.py "%USERPROFILE%\\Zomboid\\Saves" "~/Zomboid/Saves"
```

## Configuration

- `game_defaults.json` — default save paths by game and platform (Windows, Linux, Steam Deck)
- `~/game_sync_settings.json` — auto-generated at runtime; stores all user preferences, credentials, and per-machine paths

## License

MIT License