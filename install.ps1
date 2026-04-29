#Requires -Version 5.1
<#
.SYNOPSIS
    Game Sync — Installer / Updater for Windows
.DESCRIPTION
    Downloads the latest Game Sync release from GitHub, installs it to the
    user's chosen location, creates a Start Menu shortcut, optionally installs
    rclone for cloud sync, and detects existing installs for update mode.
.LINK
    https://github.com/Perezented/Game-Sync
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$Repo        = "Perezented/Game-Sync"
$BinaryName  = "game-sync.exe"
$GithubApi   = "https://api.github.com/repos/$Repo/releases/latest"
$DownloadUrl = "https://github.com/$Repo/releases/latest/download/$BinaryName"
$DefaultDir  = Join-Path $env:LOCALAPPDATA "GameSync"
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

# ── Colour helpers ────────────────────────────────────────────────────────────
function Write-Header($msg) {
    Write-Host "`n$('═' * 46)" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "$('═' * 46)`n" -ForegroundColor Cyan
}
function Write-Info($msg)    { Write-Host "  ▸ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)      { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg)    { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Err($msg)     { Write-Host "  ✗ $msg" -ForegroundColor Red }
function Ask($msg)           { Write-Host "`n  $msg" -ForegroundColor Yellow }

function Prompt-YesNo {
    param([string]$Question, [bool]$Default = $true)
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    Ask "$Question $hint"
    $ans = (Read-Host "  Choice").Trim().ToLower()
    if ($ans -eq "") { return $Default }
    return ($ans -eq "y" -or $ans -eq "yes")
}

# ── Fetch latest release version ──────────────────────────────────────────────
function Get-LatestVersion {
    try {
        $response = Invoke-RestMethod -Uri $GithubApi -UseBasicParsing -TimeoutSec 10
        return $response.tag_name
    } catch {
        Write-Warn "Could not reach GitHub API: $($_.Exception.Message)"
        return "unknown"
    }
}

# ── Download with progress ────────────────────────────────────────────────────
function Download-File {
    param([string]$Url, [string]$Dest)
    Write-Info "Downloading: $Url"
    $wc = New-Object System.Net.WebClient
    # Show progress via event
    $wc.DownloadProgressChanged += {
        $pct = $_.ProgressPercentage
        Write-Progress -Activity "Downloading $BinaryName" -PercentComplete $pct -Status "$pct%"
    }
    $task = $wc.DownloadFileTaskAsync($Url, $Dest)
    while (-not $task.IsCompleted) { Start-Sleep -Milliseconds 200 }
    Write-Progress -Activity "Downloading $BinaryName" -Completed
    if ($task.IsFaulted) { throw $task.Exception.InnerException }
}

# ── Create Start Menu shortcut ────────────────────────────────────────────────
function New-Shortcut {
    param([string]$TargetPath)
    $lnkPath = Join-Path $ShortcutDir "Game Sync.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $sc  = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath       = $TargetPath
    $sc.WorkingDirectory = Split-Path $TargetPath
    $sc.Description      = "Sync game saves across machines and cloud"
    $sc.Save()
    Write-Ok "Start Menu shortcut created: $lnkPath"
    return $lnkPath
}

# ── Create Desktop shortcut ───────────────────────────────────────────────────
function New-DesktopShortcut {
    param([string]$TargetPath)
    $lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Game Sync.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $sc  = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath       = $TargetPath
    $sc.WorkingDirectory = Split-Path $TargetPath
    $sc.Description      = "Sync game saves across machines and cloud"
    $sc.Save()
    Write-Ok "Desktop shortcut created: $lnkPath"
}

# ── Install rclone (Windows) ──────────────────────────────────────────────────
function Install-Rclone {
    param([string]$InstallDir)

    $rcloneDest = Join-Path $InstallDir "rclone.exe"

    if (Get-Command rclone -ErrorAction SilentlyContinue) {
        Write-Ok "rclone is already on PATH: $((Get-Command rclone).Source)"
        return
    }
    if (Test-Path $rcloneDest) {
        Write-Ok "rclone already exists at $rcloneDest"
        return
    }

    Write-Info "Downloading rclone …"
    $zipUrl = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
    $tmpZip = Join-Path $env:TEMP "rclone-installer.zip"
    $tmpDir = Join-Path $env:TEMP "rclone-tmp"

    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir
        $rcloneExe = Get-ChildItem -Path $tmpDir -Recurse -Filter "rclone.exe" | Select-Object -First 1
        if ($null -eq $rcloneExe) { throw "rclone.exe not found in archive." }
        Copy-Item $rcloneExe.FullName -Destination $rcloneDest -Force
        Write-Ok "rclone installed to: $rcloneDest"
        Write-Warn "rclone is in $InstallDir — make sure that folder is on your system PATH."
        Write-Info "To add it: System Settings → 'Edit environment variables' → Path → Add $InstallDir"
    } finally {
        Remove-Item $tmpZip  -ErrorAction SilentlyContinue
        Remove-Item $tmpDir  -Recurse -ErrorAction SilentlyContinue
    }
}

# ── Add folder to user PATH ───────────────────────────────────────────────────
function Add-ToUserPath {
    param([string]$Dir)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($current -notlike "*$Dir*") {
        [Environment]::SetEnvironmentVariable("Path", "$current;$Dir", "User")
        $env:Path += ";$Dir"
        Write-Ok "Added $Dir to user PATH (takes effect in new terminals)"
    } else {
        Write-Info "$Dir is already in user PATH"
    }
}

# ── Enable OpenSSH Server ─────────────────────────────────────────────────────
function Enable-SshServer {
    Write-Info "Checking SSH Server …"
    $feature = Get-WindowsCapability -Online -Name "OpenSSH.Server*" -ErrorAction SilentlyContinue
    if ($null -eq $feature) {
        Write-Warn "Could not query OpenSSH Server capability. Enable manually via Settings → Optional Features."
        return
    }
    if ($feature.State -ne "Installed") {
        Write-Info "Installing OpenSSH Server feature …"
        Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
        Write-Ok "OpenSSH Server installed."
    } else {
        Write-Ok "OpenSSH Server is already installed."
    }
    $svc = Get-Service sshd -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Write-Warn "sshd service not found after install. Reboot may be required."
        return
    }
    if ($svc.Status -ne "Running") {
        Start-Service sshd
        Write-Ok "sshd service started."
    } else {
        Write-Ok "sshd service is already running."
    }
    Set-Service -Name sshd -StartupType Automatic
    Write-Ok "sshd set to start automatically."

    # Firewall rule
    $rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
    if ($null -eq $rule) {
        New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" `
            -DisplayName "OpenSSH Server (port 22)" `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
        Write-Ok "Firewall rule created for port 22."
    } else {
        Write-Ok "Firewall rule for port 22 already exists."
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────
function Main {
    Clear-Host
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║        Game Sync  Installer          ║" -ForegroundColor Cyan
    Write-Host "  ║   github.com/Perezented/Game-Sync    ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""

    # ── OS detection (Windows only, but show info) ─────────────────────────────
    Write-Header "Step 1 — System Info"
    $osInfo = Get-CimInstance Win32_OperatingSystem
    Write-Ok "Detected: $($osInfo.Caption) ($($osInfo.OSArchitecture))"

    if ($osInfo.OSArchitecture -notlike "*64*") {
        Write-Warn "32-bit Windows detected. The Game Sync binary is 64-bit and may not run."
    }

    # ── Install location ───────────────────────────────────────────────────────
    Write-Header "Step 2 — Install Location"
    Write-Info "Default install folder: $DefaultDir"
    Ask "Press Enter to accept, or type a custom path:"
    $customDir = (Read-Host "  Path").Trim()
    $InstallDir  = if ($customDir -ne "") { $customDir } else { $DefaultDir }
    $InstallPath = Join-Path $InstallDir $BinaryName
    Write-Info "Install path: $InstallPath"

    # ── Check existing installation ────────────────────────────────────────────
    Write-Header "Step 3 — Check for Existing Installation"
    $IsUpdate = $false

    if (Test-Path $InstallPath) {
        $IsUpdate = $true
        Write-Warn "Game Sync is already installed at: $InstallPath"
        $existing = Get-Item $InstallPath
        Write-Info "Last modified: $($existing.LastWriteTime)"
    } else {
        Write-Info "No existing installation found at $InstallPath"
    }

    # ── Latest version ─────────────────────────────────────────────────────────
    Write-Info "Fetching latest release from GitHub …"
    $LatestVer = Get-LatestVersion
    if ($LatestVer -ne "unknown") {
        Write-Ok "Latest release: $LatestVer"
    }

    if ($IsUpdate) {
        $doUpdate = Prompt-YesNo "Update Game Sync to the latest release ($LatestVer)?" $true
        if (-not $doUpdate) {
            Write-Info "Update cancelled. Running post-install options only."
            Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
            return
        }
    }

    # ── Download ───────────────────────────────────────────────────────────────
    Write-Header "Step 4 — Download Game Sync"
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

    $TmpPath = Join-Path $env:TEMP $BinaryName
    try {
        Download-File -Url $DownloadUrl -Dest $TmpPath
    } catch {
        Write-Err "Download failed: $($_.Exception.Message)"
        Write-Info "Try downloading manually from:"
        Write-Info "  https://github.com/$Repo/releases/latest"
        exit 1
    }

    # Backup old binary if updating
    if ($IsUpdate -and (Test-Path $InstallPath)) {
        $backup = "$InstallPath.bak"
        Copy-Item $InstallPath $backup -Force
        Write-Info "Backed up existing binary to $backup"
    }

    Move-Item -Path $TmpPath -Destination $InstallPath -Force
    Write-Ok "Game Sync installed to: $InstallPath"

    Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
}

function Post-Install {
    param([string]$InstallPath, [string]$InstallDir, [bool]$IsUpdate)

    # ── Add to PATH ────────────────────────────────────────────────────────────
    Write-Header "Step 5 — PATH"
    if ($env:Path -notlike "*$InstallDir*") {
        $addPath = Prompt-YesNo "Add $InstallDir to your user PATH? (lets you run 'game-sync' from any terminal)" $true
        if ($addPath) { Add-ToUserPath -Dir $InstallDir }
    } else {
        Write-Ok "$InstallDir is already in your PATH"
    }

    # ── Shortcuts ──────────────────────────────────────────────────────────────
    Write-Header "Step 6 — Shortcuts"

    $makeStartMenu = Prompt-YesNo "Create a Start Menu shortcut?" $true
    if ($makeStartMenu) { New-Shortcut -TargetPath $InstallPath | Out-Null }

    $makeDesktop = Prompt-YesNo "Create a Desktop shortcut?" $false
    if ($makeDesktop) { New-DesktopShortcut -TargetPath $InstallPath }

    # ── SSH / LAN sync ─────────────────────────────────────────────────────────
    Write-Header "Step 7 — LAN Sync (SSH)"
    Write-Info "Game Sync uses SSH to sync saves between machines on your network."
    Write-Info "To sync TO this Windows machine, OpenSSH Server must be enabled."
    Write-Warn "This requires administrator privileges."

    $enableSsh = Prompt-YesNo "Enable OpenSSH Server so other machines can sync to this PC?" $false
    if ($enableSsh) {
        # Re-launch as admin if not already elevated
        $isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
                [Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $isAdmin) {
            Write-Warn "Relaunching with administrator rights for SSH setup …"
            $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -SSHOnly"
            Start-Process powershell -Verb RunAs -ArgumentList $args -Wait
        } else {
            Enable-SshServer
        }
    }

    # ── rclone / Cloud sync ────────────────────────────────────────────────────
    Write-Header "Step 8 — Cloud Sync (Google Drive / Dropbox)"
    Write-Info "Cloud sync requires rclone — a free tool."
    Write-Warn "rclone is NOT needed for LAN sync between two local machines."

    $rcloneInstalled = $false
    if (Get-Command rclone -ErrorAction SilentlyContinue) {
        $rcloneInstalled = $true
        Write-Ok "rclone is already installed: $((Get-Command rclone).Source)"
    }

    if (-not $rcloneInstalled) {
        $installRclone = Prompt-YesNo "Install rclone for Google Drive / Dropbox cloud saves?" $false
        if ($installRclone) {
            Install-Rclone -InstallDir $InstallDir
        }
    }

    # ── Summary ────────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "  $('═' * 44)" -ForegroundColor Green
    if ($IsUpdate) {
        Write-Host "  Game Sync updated successfully!" -ForegroundColor Green
    } else {
        Write-Host "  Game Sync installed successfully!" -ForegroundColor Green
    }
    Write-Host "  $('═' * 44)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Binary:   $InstallPath" -ForegroundColor White
    Write-Host ""
    Write-Host "  To run:" -ForegroundColor White
    Write-Host "    Double-click the shortcut, or run:" -ForegroundColor Gray
    Write-Host "    $InstallPath" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  If Windows Defender warns you, click More info → Run anyway." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
}

# ── Uninstall ─────────────────────────────────────────────────────────────────
function Uninstall-GameSync {
    Clear-Host
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "  ║       Game Sync  Uninstaller        ║" -ForegroundColor Red
    Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Red
    Write-Host ""

    # Search common install locations
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "GameSync\game-sync.exe"),
        (Join-Path $env:PROGRAMFILES "GameSync\game-sync.exe"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "game-sync.exe")
    )
    $foundPath = $null
    foreach ($c in $candidates) {
        if (Test-Path $c) { $foundPath = $c; break }
    }

    if (-not $foundPath) {
        Write-Warn "Game Sync not found in any standard location."
        Ask "Enter the full path to game-sync.exe (or press Enter to skip):"
        $custom = (Read-Host "  Path").Trim()
        if ($custom -ne "" -and (Test-Path $custom)) { $foundPath = $custom }
    }

    if ($foundPath) {
        Write-Info "Found binary: $foundPath"
        $removeBin = Prompt-YesNo "Remove it?" $true
        if ($removeBin) {
            # Remove binary and .bak
            Remove-Item $foundPath -Force -ErrorAction SilentlyContinue
            Remove-Item "$foundPath.bak" -Force -ErrorAction SilentlyContinue
            Write-Ok "Removed: $foundPath"
            # Remove rclone.exe in same folder if present
            $rcloneInDir = Join-Path (Split-Path $foundPath) "rclone.exe"
            if (Test-Path $rcloneInDir) {
                $removeRclone = Prompt-YesNo "Also remove rclone.exe from the same folder?" $false
                if ($removeRclone) {
                    Remove-Item $rcloneInDir -Force
                    Write-Ok "Removed: $rcloneInDir"
                }
            }
            # Remove install dir if empty
            $dir = Split-Path $foundPath
            if ((Test-Path $dir) -and ((Get-ChildItem $dir -Force | Measure-Object).Count -eq 0)) {
                Remove-Item $dir -Force
                Write-Info "Removed empty directory: $dir"
            }
        }
    } else {
        Write-Warn "No binary found — skipping binary removal."
    }

    # Remove Start Menu shortcut
    $startMenuLnk = Join-Path $ShortcutDir "Game Sync.lnk"
    if (Test-Path $startMenuLnk) {
        $removeStart = Prompt-YesNo "Remove Start Menu shortcut?" $true
        if ($removeStart) { Remove-Item $startMenuLnk -Force; Write-Ok "Removed: $startMenuLnk" }
    } else { Write-Info "No Start Menu shortcut found." }

    # Remove Desktop shortcut
    $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Game Sync.lnk"
    if (Test-Path $desktopLnk) {
        $removeDesk = Prompt-YesNo "Remove Desktop shortcut?" $true
        if ($removeDesk) { Remove-Item $desktopLnk -Force; Write-Ok "Removed: $desktopLnk" }
    } else { Write-Info "No Desktop shortcut found." }

    # Remove settings file
    $settings = Join-Path $env:APPDATA "game_sync_settings.json"
    if (Test-Path $settings) {
        $removeSettings = Prompt-YesNo "Remove saved settings ($settings)?" $false
        if ($removeSettings) { Remove-Item $settings -Force; Write-Ok "Removed: $settings" }
        else { Write-Info "Settings kept at $settings" }
    }

    # Remove from user PATH
    if ($foundPath) {
        $installDir = Split-Path $foundPath
        $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($currentPath -like "*$installDir*") {
            $removePath = Prompt-YesNo "Remove $installDir from user PATH?" $true
            if ($removePath) {
                $newPath = ($currentPath -split ';' | Where-Object { $_ -ne $installDir }) -join ';'
                [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
                Write-Ok "Removed $installDir from user PATH"
            }
        }
    }

    Write-Host ""
    Write-Host "  $('═' * 44)" -ForegroundColor Green
    Write-Host "  Game Sync uninstalled." -ForegroundColor Green
    Write-Host "  $('═' * 44)" -ForegroundColor Green
    Write-Host ""
    Read-Host "  Press Enter to exit"
}

# ── Entry point ───────────────────────────────────────────────────────────────
param([switch]$SSHOnly, [switch]$Uninstall)
if ($SSHOnly) {
    Enable-SshServer
    Read-Host "Press Enter to exit"
    exit 0
}
if ($Uninstall) {
    Uninstall-GameSync
    exit 0
}

Main
