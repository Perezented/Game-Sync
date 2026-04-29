# Game Sync App

A PyQt6 GUI utility for keeping game save files in sync across machines and platforms. Supports any game without native Steam Cloud Saves and can be extended to additional titles.

## Features

- **LAN machine sync** — scans the local network for reachable machines, selects a destination by MAC address, and syncs saves directly over SSH/rsync
- **Cloud sync** — push/pull saves to Google Drive or Dropbox via [rclone](https://rclone.org/) (no developer accounts required)
- **Cross-platform path handling** — supports Linux `~/` paths and Windows `%USERPROFILE%`/`%APPDATA%` paths with automatic expansion
- **Per-game, per-machine settings** — source/destination paths and sync direction are remembered per game per machine (keyed by MAC address)
- **Persistent settings** — all configuration is saved to `~/game_sync_settings.json` and restored on next launch, including the last-used destination machine
- **SSH credential storage** — username, SSH key, and port saved per destination machine; optional password entry via secure dialog
- **Sync direction control** — choose between Linux↔Linux, Linux↔Windows, Windows↔Linux, or Windows↔Windows
- **Dark theme UI** — scrollable settings panel with grouped sections for cloud and LAN configuration
- **Standalone binary** — can be packaged with PyInstaller into a single executable

## Supported Games

- Conan Exiles
- Dark Souls II
- Dark Souls III
- Fallout 3
- Grand Theft Auto V
- Just Cause 3
- Mass Effect Andromeda
- Minecraft Java
- Project Zomboid
- Red Dead Redemption 2
- Sleeping Dogs
- Star Wars Jedi: Fallen Order
- Tale of Two Wastelands

## Contents

- `game-sync.py` — application entry point
- `ui_app.py` — PyQt6 GUI (`SyncApp` main window)
- `cloud_sync.py` — rclone-based Google Drive and Dropbox sync
- `local_network_sync.py` — SSH/rsync LAN sync and connection testing
- `network_scanner.py` — local /24 subnet scanner
- `game_defaults.py` — default save paths per game and platform
- `sync_engine.py` — standalone path expansion and file copy logic (CLI)
- `config.json` — example sync entries
- `mock_data/` — example save folder structure for development and testing

## Requirements

- Python 3.10+
- PyQt6

### Optional

- [rclone](https://rclone.org/install/) — required for Google Drive and Dropbox cloud sync
- `paramiko` — required for SSH-based LAN sync
- `sshpass` — required for password-based SSH authentication (Linux package)

## Installation

### Linux / Windows

1. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. (Optional) Install rclone for cloud sync — see [rclone setup](#rclone-setup-optional) below.

### Steam Deck

The Steam Deck runs a read-only OS. All steps are done in **Desktop Mode**.

1. Open a terminal (Konsole) and install Python if not already present:

```bash
# Check if Python 3 is available
python3 --version
```

If Python 3 is missing, install it via Flatpak or the Discover store. Alternatively, use the built-in Python from a Flatpak environment like [Flatseal](https://flathub.org/apps/com.github.tchx84.Flatseal).

2. Create a virtual environment in your home directory:

```bash
python3 -m venv ~/game-sync-venv
source ~/game-sync-venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Run the app:

```bash
python game-sync.py
```

5. (Optional) To launch from Game Mode, create a non-Steam shortcut:
   - In Steam Desktop Mode, go to **Add a Game → Add a Non-Steam Game**
   - Point it to a launch script, e.g. `~/game-sync-launch.sh`:

```bash
#!/bin/bash
source ~/game-sync-venv/bin/activate
python ~/zomboid-sync-app/game-sync.py
```

Make it executable: `chmod +x ~/game-sync-launch.sh`

---

## rclone Setup (Optional)

rclone is required for Google Drive and Dropbox cloud sync. The app uses rclone's built-in OAuth credentials — no developer account needed.

### Linux / Steam Deck

```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
```

> **Steam Deck note:** The OS is read-only, so `sudo` writes to an overlay. rclone will be lost after a system update. Re-run the install command after SteamOS updates, or install rclone to `~/.local/bin` instead:
>
> ```bash
> mkdir -p ~/.local/bin
> curl https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip
> unzip /tmp/rclone.zip -d /tmp/rclone-tmp
> cp /tmp/rclone-tmp/rclone-*/rclone ~/.local/bin/rclone
> chmod +x ~/.local/bin/rclone
> ```
>
> Then add `~/.local/bin` to your PATH in `~/.bashrc`:
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

### Windows

Download the installer from [https://rclone.org/install/](https://rclone.org/install/) and add rclone to your system PATH.

### Authorizing Google Drive or Dropbox

Once rclone is installed, authorization is done inside the app:

1. Open the app and enable **Cloud Sync**
2. Select **Google Drive** or **Dropbox**
3. Click **Authorize** — your browser will open for a standard login
4. Once authorized, the token is saved and reused automatically

## Running the App

```bash
python game-sync.py
```

## Building a Standalone Executable

Requires [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=app_icon.ico game-sync.py
```

The output binary will be in `dist/game-sync`.

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

- `game_defaults.py` — default save paths by game and platform (Windows, Linux, Steam Deck)
- `~/game_sync_settings.json` — auto-generated at runtime; stores all user preferences, credentials, and per-machine paths

## License

MIT License
