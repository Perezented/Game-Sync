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

## Easy Install (No Coding Needed)

Download the latest prebuilt binary from the [Releases page](https://github.com/Perezented/Game-Sync/releases) — no Python install required.

### What to download

- **Windows:** `game-sync.exe`
- **Linux / Steam Deck:** `game-sync` (no file extension)

### Installer / Updater Scripts

The repo includes interactive installer scripts that handle downloading, placement, desktop launchers, optional SSH setup, and optional rclone (cloud sync). They also detect an existing install and offer to update it.

#### Linux / Steam Deck — `install.sh`

```bash
curl -fsSL https://raw.githubusercontent.com/Perezented/Game-Sync/main/install.sh | bash
```

Or download [install.sh](install.sh) and run it locally:

```bash
bash install.sh
```

The script will:
- Auto-detect your OS (Steam Deck, Arch, Ubuntu, Fedora, openSUSE, etc.) with a manual override option
- Download the correct binary and place it in `~/Applications` (Steam Deck) or `~/.local/bin` (Linux)
- Create a `~/.local/share/applications/game-sync.desktop` launcher
- Optionally add the install directory to `PATH`
- Optionally enable SSH so other machines can sync to this one
- Optionally install rclone for Google Drive / Dropbox cloud saves (home-directory method on Steam Deck)
- Detect an existing install and offer to update it

#### Windows — `install.ps1`

Open **PowerShell** (no admin required unless you choose to enable SSH) and run:

```powershell
irm https://raw.githubusercontent.com/Perezented/Game-Sync/main/install.ps1 | iex
```

Or download [install.ps1](install.ps1) and run it:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The script will:
- Download `game-sync.exe` to `%LOCALAPPDATA%\GameSync\` (customisable)
- Add the install folder to your user `PATH`
- Create a Start Menu shortcut (and optionally a Desktop shortcut)
- Optionally enable OpenSSH Server (self-elevates to admin as needed)
- Optionally install rclone for cloud saves
- Detect an existing install and offer to update it

---

## Uninstalling

### Linux / Steam Deck

Run the installer script with the `--uninstall` flag:

```bash
bash install.sh --uninstall
```

Or manually:

```bash
# Remove binary (Steam Deck default location)
rm -f ~/Applications/game-sync ~/Applications/game-sync.bak

# Remove binary (Linux default location)
rm -f ~/.local/bin/game-sync ~/.local/bin/game-sync.bak

# Remove desktop launcher
rm -f ~/.local/share/applications/game-sync.desktop

# Remove saved settings (optional — contains your sync paths and preferences)
rm -f ~/game_sync_settings.json
```

### Windows

Run the installer script with the `-Uninstall` flag:

```powershell
.\install.ps1 -Uninstall
```

Or manually:

1. Delete `%LOCALAPPDATA%\GameSync\` (or wherever you installed it)
2. Delete shortcuts:
   - Start Menu: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Game Sync.lnk`
   - Desktop: `%USERPROFILE%\Desktop\Game Sync.lnk`
3. Remove from PATH: **Settings → System → About → Advanced system settings → Environment Variables** → edit `Path` under *User variables* and remove the GameSync entry
4. Delete saved settings (optional): `%APPDATA%\game_sync_settings.json`

---

## Windows (Desktop App)

1. Download `game-sync.exe` from the [Releases page](https://github.com/Perezented/Game-Sync/releases).
2. Double-click `game-sync.exe`.
3. If SmartScreen appears:
    - Click **More info** → **Run anyway**.
4. (Optional) Right-click `game-sync.exe` → **Send to → Desktop (create shortcut)**.

That is it. No Python install required.

---

## Steam Deck (Desktop Mode App)

### Quick Install (recommended)

Switch to **Desktop Mode**, open **Konsole**, and run:

```bash
curl -fsSL https://raw.githubusercontent.com/Perezented/Game-Sync/main/install.sh | bash
```

The installer handles everything: OS detection, binary download, desktop launcher, optional SSH, and optional rclone. It also detects an existing install and offers to update it.

After it finishes, launch from **Konsole** (`~/Applications/game-sync`) or find **Game Sync** in the KDE app menu.

### Add to Steam (Game Mode)

1. In **Steam (Desktop Mode)** click **Games → Add a Non-Steam Game to My Library**
2. Click **Browse**, navigate to `~/Applications/` and select `game-sync`
3. Click **Add Selected Programs**

### Manual Install

1. Switch to **Desktop Mode** (hold the Power button → *Switch to Desktop*)
2. Download `game-sync` from the [Releases page](https://github.com/Perezented/Game-Sync/releases)
3. Open **Konsole** and run:

```bash
mkdir -p ~/Applications
mv ~/Downloads/game-sync ~/Applications/game-sync
chmod +x ~/Applications/game-sync
```

4. (Optional) Create a desktop launcher so it appears in the KDE app menu:

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/game-sync.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Game Sync
Exec=/home/deck/Applications/game-sync
Icon=utilities-terminal
Terminal=false
Categories=Utility;
EOF
```

5. Run it: `~/Applications/game-sync`

---

## Linux Distros (Desktop App)

1. Download `game-sync` from the [Releases page](https://github.com/Perezented/Game-Sync/releases)
2. Open a terminal in the folder where you saved it and make it executable:

```bash
chmod +x game-sync
./game-sync
```

Or double-click it in your file manager if it supports running executables.

(Optional) To install it system-wide and add an app menu entry:

```bash
mkdir -p ~/.local/bin
mv game-sync ~/.local/bin/game-sync
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/game-sync.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Game Sync
Exec=/home/$USER/.local/bin/game-sync
Icon=utilities-terminal
Terminal=false
Categories=Utility;
EOF
```

Then launch **Game Sync** from your app menu or run `~/.local/bin/game-sync`.

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

5. (Optional) To launch from Game Mode:
   - In Steam Desktop Mode, go to **Games → Add a Non-Steam Game to My Library**
   - Click **Browse** and select your `game-sync.py` or create a small shell script that activates the venv and runs it

---

## rclone Setup (Optional)

rclone is required for Google Drive and Dropbox cloud sync. The app uses rclone's built-in OAuth credentials — no developer account needed.

### Linux / Steam Deck

```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
```

> **Steam Deck:** Install rclone to your home directory so it survives SteamOS updates:
>
> ```bash
> mkdir -p ~/.local/bin
> curl https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip
> unzip /tmp/rclone.zip -d /tmp/rclone-tmp
> cp /tmp/rclone-tmp/rclone-*/rclone ~/.local/bin/rclone
> chmod +x ~/.local/bin/rclone
> echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
> source ~/.bashrc
> ```
>
> Installing via `sudo pacman -S rclone` also works but rclone will be removed after a SteamOS system update. The home-directory method above is more persistent.

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
```

Use the included spec file (bundles `app_icon.png` correctly):

```bash
python -m PyInstaller game-sync.spec
```

Or build manually (icon asset will need to be present):

```bash
pyinstaller --onefile --windowed --icon=app_icon.ico game-sync.py
```

The output binary will be in `dist/`.

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
