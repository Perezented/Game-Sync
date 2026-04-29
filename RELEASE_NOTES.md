# Game Sync — Release Notes

## v1.0.0 — April 29, 2026

### What is Game Sync?

Game Sync lets you keep your game save files in sync between two computers — for example, your Windows gaming PC and a Linux machine, or any two machines on the same home network. It also supports backing up your saves to Google Drive or Dropbox so you can access them from anywhere.

---

### What's New in v1.0.0

- Sync game saves directly between two computers on your home network
- Back up and restore saves using Google Drive or Dropbox
- Automatically detects other machines on your network
- Remembers your settings per game and per machine
- Works across Windows and Linux (including mixed setups)
- Secure password entry — passwords are never saved to disk
- Built-in sync log so you can see exactly what was transferred

---

## Windows

### Installer (recommended)

Open **PowerShell** and run:

```powershell
irm https://raw.githubusercontent.com/Perezented/Game-Sync/main/install.ps1 | iex
```

The installer will guide you through placement, shortcuts, optional SSH setup, and optional rclone for cloud saves. It also detects an existing install and offers to update it.

### Manual Download & Run

1. Download **`game-sync.exe`** from the releases page
2. Double-click it — no installation needed
3. If Windows Defender shows a warning, click **More info → Run anyway**
   *(This appears because the app is not yet code-signed. It is safe to run.)*

That's it. No Python, no command line, nothing else to install.

> **Your settings** are saved automatically to `%APPDATA%\game_sync_settings.json`

---

## Linux

### Installer (recommended)

Open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/Perezented/Game-Sync/main/install.sh | bash
```

The installer detects your distro, downloads the binary, creates a `.desktop` launcher, and optionally sets up SSH and rclone.

### Manual Download & Run

1. Download **`game-sync`** from the releases page
2. Open a terminal in the folder where you saved it and make it executable:

```bash
chmod +x game-sync
./game-sync
```

Or double-click it in your file manager if it supports running executables.

No Python installation required — everything is bundled.

> **Your settings** are saved automatically to `~/game_sync_settings.json`

---

## Steam Deck

### Quick Install (recommended)

Switch to **Desktop Mode**, open **Konsole**, and run:

```bash
curl -fsSL https://raw.githubusercontent.com/Perezented/Game-Sync/main/install.sh | bash
```

The installer will:
- Detect Steam Deck automatically (with a manual override option)
- Download the binary to `~/Applications/game-sync`
- Create a KDE desktop launcher (app menu entry)
- Optionally enable SSH for LAN sync
- Optionally install rclone for cloud saves
- Detect an existing install and offer to update it

After it finishes:
- Launch it from **Konsole**: `~/Applications/game-sync`
- Or find **Game Sync** in the application launcher (KDE menu)
- Or add it to Steam — see below

No Python installation required — everything is bundled.

> **Your settings** are saved automatically to `~/game_sync_settings.json`

### Add to Steam (launch from Game Mode)

1. In **Steam (Desktop Mode)** click **Games → Add a Non-Steam Game to My Library**
2. Click **Browse**, navigate to `~/Applications/` and select `game-sync`
3. Click **Add Selected Programs**
4. You can now launch Game Sync from Game Mode or the Steam library

### Manual Install (alternative)

If you prefer to do it step by step:

1. Switch to **Desktop Mode** (hold the Power button → *Switch to Desktop*)
2. Open the **browser** (Firefox is pre-installed) and download **`game-sync`** from the releases page
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

5. Run it:

```bash
~/Applications/game-sync
```
- or -
Just launch from the KDE menu (click the bottom-left icon and search for "Game Sync")


### SSH on Steam Deck (for LAN sync)

If you want to sync **to** your Steam Deck from another machine, enable SSH:

```bash
sudo systemctl enable --now sshd
```

The default username is **`deck`**. Set a password for it if you haven't already:

```bash
passwd
```

> **Note:** SteamOS is updated occasionally and may reset some system changes. Re-enable SSH with the same command after a major SteamOS update if it stops working.

### rclone on Steam Deck (for Cloud sync)

Install rclone to your home directory so it survives SteamOS updates:

```bash
mkdir -p ~/.local/bin
curl https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rclone.zip
unzip /tmp/rclone.zip -d /tmp/rclone-tmp
cp /tmp/rclone-tmp/rclone-*/rclone ~/.local/bin/rclone
chmod +x ~/.local/bin/rclone
```

Then add `~/.local/bin` to your PATH so Game Sync can find it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

> Installing via `sudo pacman -S rclone` also works but rclone will be removed after a SteamOS system update. The home-directory method above is more persistent.

---

## Syncing Between Two Computers

### Direct LAN Sync (no internet needed)

This syncs saves directly between two computers on the same Wi-Fi or wired network.

**Requirements on the destination machine:**

#### Linux
SSH server is usually already installed. If not, install it with one command:
- Ubuntu / Debian / Mint: `sudo apt install openssh-server`
- Fedora: `sudo dnf install openssh-server && sudo systemctl enable --now sshd`
- Arch / Manjaro / SteamOS: `sudo pacman -S openssh && sudo systemctl enable --now sshd`
- openSUSE: `sudo zypper install openssh && sudo systemctl enable --now sshd`

#### Windows — Enabling OpenSSH Server

This is required if you want to sync **to** a Windows machine (from Linux or another Windows PC).

**1. Install OpenSSH Server**

- Open **Settings → System → Optional Features**
- Click **Add a feature**
- Search for **OpenSSH Server** and click **Install**

**2. Start the service and set it to run automatically**

**Option A — Using the Services app (no command line)**

1. Press **Win + R**, type `services.msc`, and press Enter
2. Scroll down and find **OpenSSH SSH Server**
3. Double-click it to open its properties
4. Set **Startup type** to **Automatic**
5. Click **Start** to start it now, then click **OK**

**Option B — Using PowerShell**

Open **PowerShell as Administrator** (right-click the Start menu → *Windows PowerShell (Admin)*) and run:

```powershell
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

You can verify it's running with:
```powershell
Get-Service sshd
```
It should say `Running`.

**3. Allow SSH through Windows Firewall**

The installer usually adds this rule automatically, but if connections are being refused, add it manually using one of the options below.

**Option A — Using Windows Defender Firewall (no command line)**

1. Press **Win + R**, type `wf.msc`, and press Enter — this opens **Windows Defender Firewall with Advanced Security**
2. In the left panel, click **Inbound Rules**
3. In the right panel, click **New Rule…**
4. Select **Port** and click **Next**
5. Leave **TCP** selected, enter `22` in the **Specific local ports** field, and click **Next**
6. Select **Allow the connection** and click **Next**
7. Check all three boxes — **Domain**, **Private**, and **Public** — and click **Next**
8. Give the rule a name such as `OpenSSH Server (port 22)` and click **Finish**

**Option B — Using PowerShell**

Open **PowerShell as Administrator** and run:

```powershell
New-NetFirewallRule -Name "OpenSSH-Server" -DisplayName "OpenSSH Server (port 22)" `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

To confirm the rule was created:
```powershell
Get-NetFirewallRule -Name "OpenSSH-Server"
```

> **Note:** If your network is set to **Public** rather than **Private** or **Domain**, Windows may still block the connection even with a firewall rule. To fix this, go to **Settings → Network & Internet**, click your connection, and set the network profile to **Private**.

**How to use:**
1. Open Game Sync on the machine you want to sync **from**
2. Select your game from the dropdown
3. Click **Scan Network** — nearby machines will appear in the list
4. Select the destination machine
5. Enter its SSH username and password (or SSH key)
6. Click **Test Connection** to confirm it works
7. Click **Push to Dest** to send your saves, or **Pull from Dest** to receive them

---

## Cloud Sync — Google Drive & Dropbox (Optional)

Cloud sync lets you back up saves to Google Drive or Dropbox and restore them on any machine. This is great if your two computers are not on the same network, or as an extra backup.

**You do not need a developer account.** Game Sync handles authorization through your normal Google or Dropbox login.

### Step 1 — Install rclone

Cloud sync requires a free tool called **rclone**. Install it once and Game Sync handles the rest.

#### Windows

1. Go to [rclone.org/downloads](https://rclone.org/downloads/) and download the **Windows 64-bit** zip
2. Open the zip and copy `rclone.exe` into `C:\Windows\System32\`
3. That's it — no setup wizard needed

#### Linux — Ubuntu / Debian / Mint / Pop!_OS

```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
```

#### Linux — Arch / Manjaro / SteamOS

```bash
sudo pacman -S rclone
```

#### Linux — Fedora

```bash
sudo dnf install rclone
```

#### Linux — openSUSE

```bash
sudo zypper install rclone
```

#### Steam Deck

See the [rclone on Steam Deck](#rclone-on-steam-deck-for-cloud-sync) section above for the persistent install method.

### Step 2 — Connect Your Account in Game Sync

1. Open Game Sync and check **Enable Cloud Storage**
2. Select **Google Drive**, **Dropbox**, or **Both**
3. Click **Authorize Google Drive** (or **Authorize Dropbox**)
4. Your browser will open — sign in with your Google or Dropbox account and click Allow
5. Game Sync saves the token locally. You won't need to do this again unless you log out.

### Step 3 — Sync

- Click **Push to Cloud** to upload your saves
- Click **Pull from Cloud** to download them on another machine

---

## Tips

- **First time?** Select your game, hit Scan Network, pick your other machine, test the connection, and push/pull. That's the whole workflow.
- **Mixed Windows/Linux?** Game Sync automatically handles the different save path formats on each side.
- **Settings are remembered** — next time you open the app your game, machine, and paths will all be pre-filled.
- **Sync log** — the panel at the bottom shows exactly what files were sent or skipped during each sync.
- **Steam Deck users** — use the home-directory rclone install and the SSH enable command so things survive SteamOS updates.

---

## Known Limitations

- Direct LAN sync requires both computers to be on the same home network
- The network scanner searches your local `/24` subnet (e.g. `192.168.1.x`) only
- On Linux, password-based SSH requires `sshpass` to be installed (`sudo apt install sshpass` etc.) — key-based auth does not need it
- On Steam Deck, system-level packages (`pacman -S …`) may be removed after a SteamOS update — prefer home-directory installs for tools like rclone
