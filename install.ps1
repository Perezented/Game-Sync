#Requires -Version 5.1
<#
.SYNOPSIS
    Game Sync - Installer / Updater for Windows
.DESCRIPTION
    Downloads the latest Game Sync release from GitHub, installs it to the
    user's chosen location, creates a Start Menu shortcut, optionally installs
    rclone for cloud sync, and detects existing installs for update mode.
.LINK
    https://github.com/Perezented/Game-Sync
#>
param([switch]$SSHOnly, [switch]$Uninstall)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# -- Config ------------------------------------------------------------------
$Repo        = "Perezented/Game-Sync"
$BinaryName  = "game-sync.exe"
$GithubApi   = "https://api.github.com/repos/$Repo/releases/latest"
$DownloadUrl = "https://github.com/$Repo/releases/latest/download/$BinaryName"
$DownloadUserAgent = "Game-Sync-Installer"
$DefaultDir  = Join-Path $env:LOCALAPPDATA "GameSync"
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

function Get-InstalledVersionFile {
    param([string]$InstallPath)
    return "$InstallPath.version"
}

# -- Color helpers ------------------------------------------------------------
function Write-Header($msg) {
    Write-Host "`n$('=' * 46)" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "$('=' * 46)`n" -ForegroundColor Cyan
}
function Write-Info($msg)    { Write-Host "  [*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)      { Write-Host "  [+] $msg" -ForegroundColor Green }
function Write-Warn($msg)    { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)     { Write-Host "  [x] $msg" -ForegroundColor Red }
function Ask($msg)           { Write-Host "`n  $msg" -ForegroundColor Yellow }

function Prompt-YesNo {
    param([string]$Question, [bool]$Default = $true)
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    Ask "$Question $hint"
    $ans = (Read-Host "  Choice").Trim().ToLower()
    if ($ans -eq "") { return $Default }
    return ($ans -eq "y" -or $ans -eq "yes")
}

# -- Version helpers ----------------------------------------------------------
function Get-LatestVersion {
    try {
        $response = Invoke-RestMethod -Uri $GithubApi -UseBasicParsing -TimeoutSec 10
        return $response.tag_name
    } catch {
        Write-Warn "Could not reach GitHub API: $($_.Exception.Message)"
        return "unknown"
    }
}

function Normalize-VersionValue {
    param(
        [AllowNull()][string]$Version,
        [string]$Fallback = "unknown"
    )

    if ([string]::IsNullOrWhiteSpace($Version)) {
        return $Fallback
    }

    return $Version.Trim()
}

function Test-KnownVersion {
    param([string]$Version)

    return (Normalize-VersionValue $Version).ToLowerInvariant() -ne "unknown"
}

function Enable-Tls12IfAvailable {
    $tls12 = [Net.SecurityProtocolType]::Tls12
    if (([Net.ServicePointManager]::SecurityProtocol -band $tls12) -eq 0) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor $tls12
    }
}

# -- Download with progress ---------------------------------------------------
function Download-File {
    param([string]$Url, [string]$Dest)

    Write-Info "Downloading: $Url"

    Enable-Tls12IfAvailable

    $destDir = Split-Path -Parent $Dest
    if (-not [string]::IsNullOrWhiteSpace($destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -MaximumRedirection 10 -Headers @{ "User-Agent" = $DownloadUserAgent }
        return
    } catch {
        $webError = $_
        Write-Warn "Invoke-WebRequest download failed: $($webError.Exception.Message)"
    }

    if (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue) {
        Write-Info "Retrying download with BITS ..."
        try {
            Start-BitsTransfer -Source $Url -Destination $Dest -DisplayName "Game Sync Installer" -Description "Downloading $BinaryName"
            return
        } catch {
            throw "Download failed with both Invoke-WebRequest and BITS. Last error: $($_.Exception.Message)"
        }
    }

    throw "Download failed and BITS is not available on this system. Invoke-WebRequest error: $($webError.Exception.Message)"
}

# -- Create Start Menu shortcut -----------------------------------------------
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

# -- Create Desktop shortcut --------------------------------------------------
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

# -- Install rclone (Windows) -------------------------------------------------
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

    Write-Info "Downloading rclone ..."
    $zipUrl = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
    $tmpRoot = Join-Path $env:TEMP ("rclone-tmp-" + [guid]::NewGuid().ToString("N"))
    $tmpZip = Join-Path $tmpRoot "rclone-installer.zip"
    $tmpDir = Join-Path $tmpRoot "extract"

    try {
        Download-File -Url $zipUrl -Dest $tmpZip
        Write-Info "Extracting rclone archive ..."
        Expand-Archive -Path $tmpZip -DestinationPath $tmpDir
        Write-Info "Installing rclone ..."
        $rcloneExe = Get-ChildItem -Path $tmpDir -Recurse -Filter "rclone.exe" | Select-Object -First 1
        if ($null -eq $rcloneExe) { throw "rclone.exe not found in archive." }
        Copy-Item $rcloneExe.FullName -Destination $rcloneDest -Force
        Write-Ok "rclone installed to: $rcloneDest"
        Write-Warn "rclone is in $InstallDir - make sure that folder is on your system PATH."
        Write-Info "To add it: System Settings → 'Edit environment variables' → Path → Add $InstallDir"
    } finally {
        Remove-Item $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# -- Path helper functions ---------------------------------------------------
function Get-PathEntries {
    param([string]$PathValue)
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return @()
    }
    return $PathValue -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }
}

function PathContainsEntry {
    param(
        [string]$PathValue,
        [string]$Entry
    )
    $entries = Get-PathEntries $PathValue
    return $entries -contains $Entry
}

# -- Add folder to user PATH --------------------------------------------------
function Add-ToUserPath {
    param([string]$Dir)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (PathContainsEntry -PathValue $current -Entry $Dir)) {
        $newPath = if ([string]::IsNullOrWhiteSpace($current)) { $Dir } else { "$current;$Dir" }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path += ";$Dir"
        Write-Ok "Added $Dir to user PATH (takes effect in new terminals)"
    } else {
        Write-Info "$Dir is already in user PATH"
    }
}

function Remove-PathSafely {
    param(
        [string]$Path,
        [string]$Label,
        [switch]$Recurse
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return $true
    }

    try {
        $removeParams = @{
            LiteralPath = $Path
            Force = $true
            ErrorAction = 'Stop'
        }
        if ($Recurse) {
            $removeParams.Recurse = $true
        }

        Remove-Item @removeParams
        return $true
    } catch {
        Write-Warn "Could not remove ${Label}: $($_.Exception.Message)"
        return $false
    }
}

function Add-UniqueValue {
    param(
        [System.Collections.ArrayList]$List,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }

    if (-not $List.Contains($Value)) {
        [void]$List.Add($Value)
    }
}

function Test-IsAdmin {
    return ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Get-ActiveFirewallProfiles {
    $profiles = New-Object System.Collections.ArrayList

    try {
        $connectionProfiles = Get-NetConnectionProfile -ErrorAction SilentlyContinue
        foreach ($connectionProfile in @($connectionProfiles)) {
            switch ($connectionProfile.NetworkCategory) {
                'DomainAuthenticated' { Add-UniqueValue -List $profiles -Value 'Domain' }
                'Private'             { Add-UniqueValue -List $profiles -Value 'Private' }
                'Public'              { Add-UniqueValue -List $profiles -Value 'Public' }
            }
        }
    } catch {
    }

    if ($profiles.Count -eq 0) {
        try {
            $enabledProfiles = Get-NetFirewallProfile -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' }
            foreach ($firewallProfile in @($enabledProfiles)) {
                Add-UniqueValue -List $profiles -Value $firewallProfile.Name
            }
        } catch {
        }
    }

    return @($profiles)
}

function Test-FirewallRuleAppliesToActiveProfile {
    param(
        $Rule,
        [string[]]$ActiveProfiles
    )

    if ($null -eq $Rule) {
        return $false
    }

    if (($Rule.Profile -eq 'Any') -or ($Rule.Profile -eq 0)) {
        return $true
    }

    $ruleProfiles = @($Rule.Profile.ToString().Split(',') | ForEach-Object { $_.Trim() } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if (@($ruleProfiles).Count -eq 0) {
        return $true
    }

    foreach ($activeProfile in @($ActiveProfiles)) {
        if ($ruleProfiles -contains $activeProfile) {
            return $true
        }
    }

    return $false
}

function Test-OpenSshFirewallRule {
    param(
        $Rule,
        [string[]]$ActiveProfiles
    )

    if ($null -eq $Rule) {
        return $false
    }

    if (-not (Test-FirewallRuleAppliesToActiveProfile -Rule $Rule -ActiveProfiles $ActiveProfiles)) {
        return $false
    }

    $applicationFilter = $Rule | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Select-Object -First 1
    $serviceFilter = $Rule | Get-NetFirewallServiceFilter -ErrorAction SilentlyContinue | Select-Object -First 1

    $program = $null
    if ($null -ne $applicationFilter) {
        $program = $applicationFilter.Program
    }

    $serviceName = $null
    if ($null -ne $serviceFilter) {
        $serviceName = $serviceFilter.Service
    }

    $programIsUnrestricted = [string]::IsNullOrWhiteSpace($program) -or $program -eq 'Any'
    $serviceIsUnrestricted = [string]::IsNullOrWhiteSpace($serviceName) -or $serviceName -eq 'Any'
    $matchesSshdProgram = (-not [string]::IsNullOrWhiteSpace($program)) -and ($program -match '(?i)(^|[\\/])sshd\.exe$')
    $matchesSshdService = (-not [string]::IsNullOrWhiteSpace($serviceName)) -and ($serviceName -ieq 'sshd')

    return ($programIsUnrestricted -or $matchesSshdProgram) -and ($serviceIsUnrestricted -or $matchesSshdService)
}

function Get-SshServerStatus {
    $capability = $null
    $service = Get-Service sshd -ErrorAction SilentlyContinue
    $activeProfiles = Get-ActiveFirewallProfiles
    $capabilityQueryFailed = $false
    $firewallQueryFailed = $false
    $port22Rules = @()

    try {
        $capability = Get-WindowsCapability -Online -Name "OpenSSH.Server*" -ErrorAction Stop | Select-Object -First 1
    } catch {
        $capabilityQueryFailed = $true
    }

    try {
        $port22Rules = Get-NetFirewallPortFilter -Protocol TCP -ErrorAction Stop |
            Where-Object { $_.LocalPort -eq 22 } |
            ForEach-Object {
                Get-NetFirewallRule -AssociatedNetFirewallPortFilter $_ -ErrorAction SilentlyContinue
            } |
            Where-Object {
                $_.Direction -eq 'Inbound' -and
                $_.Action -eq 'Allow' -and
                $_.Enabled -eq 'True' -and
                (Test-OpenSshFirewallRule -Rule $_ -ActiveProfiles $activeProfiles)
            }
    } catch {
        $firewallQueryFailed = $true
    }

    $isInstalled = ($null -ne $capability) -and ($capability.State -eq "Installed")
    $isRunning = ($null -ne $service) -and ($service.Status -eq "Running")
    $isAutomatic = ($null -ne $service) -and ($service.StartType -eq "Automatic")
    $firewallAllows22 = @($port22Rules).Count -gt 0

    return [pscustomobject]@{
        Installed        = $isInstalled
        Running          = $isRunning
        Automatic        = $isAutomatic
        FirewallAllows22 = $firewallAllows22
        CapabilityQueryFailed = $capabilityQueryFailed
        FirewallQueryFailed = $firewallQueryFailed
        Ready            = $isInstalled -and $isRunning -and $isAutomatic -and $firewallAllows22
    }
}

function Get-OpenSshFirewallRule {
    return Get-NetFirewallRule -Name "OpenSSH Server (sshd)" -ErrorAction SilentlyContinue | Select-Object -First 1
}

# -- Enable OpenSSH Server ----------------------------------------------------
function Enable-SshServer {
    Write-Info "Checking SSH Server ..."
    $feature = Get-WindowsCapability -Online -Name "OpenSSH.Server*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $feature) {
        Write-Warn "Could not query OpenSSH Server capability. Enable manually via Settings → Optional Features."
        return
    }
    if ($feature.State -ne "Installed") {
        Write-Info "Installing OpenSSH Server feature ..."
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
    $rule = Get-NetFirewallRule -Name "OpenSSH Server (sshd)" -ErrorAction SilentlyContinue
    if ($null -eq $rule) {
        New-NetFirewallRule -Name "OpenSSH Server (sshd)" `
            -DisplayName "OpenSSH Server (port 22)" `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
        Write-Ok "Firewall rule created for port 22."
    } else {
        Write-Ok "Firewall rule for port 22 already exists."
    }
}

# -- Main ---------------------------------------------------------------------
function Main {
    Clear-Host
    Write-Host ""
    Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
    Write-Host "  |        Game Sync  Installer          |" -ForegroundColor Cyan
    Write-Host "  |   github.com/Perezented/Game-Sync    |" -ForegroundColor Cyan
    Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
    Write-Host ""

    # -- OS detection (Windows only, but show info) ----------------------------
    Write-Header "Step 1 - System Info"
    $osInfo = Get-CimInstance Win32_OperatingSystem
    Write-Ok "Detected: $($osInfo.Caption) ($($osInfo.OSArchitecture))"

    if ($osInfo.OSArchitecture -notlike "*64*") {
        Write-Warn "32-bit Windows detected. The Game Sync binary is 64-bit and may not run."
    }

    # -- Install location ------------------------------------------------------
    Write-Header "Step 2 - Install Location"
    Write-Info "Default install folder: $DefaultDir"
    Ask "Press Enter to accept, or type a custom path:"
    $customDir = (Read-Host "  Path").Trim()
    $InstallDir  = if ($customDir -ne "") { $customDir } else { $DefaultDir }
    $InstallPath = Join-Path $InstallDir $BinaryName
    Write-Info "Install path: $InstallPath"

    # -- Check existing installation -------------------------------------------
    Write-Header "Step 3 - Check for Existing Installation"
    $IsUpdate = $false
    $InstalledVersion = "none"
    $InstalledVersionFile = Get-InstalledVersionFile -InstallPath $InstallPath

    if (Test-Path $InstallPath) {
        $IsUpdate = $true
        Write-Warn "Game Sync is already installed at: $InstallPath"
        if (Test-Path $InstalledVersionFile) {
            $InstalledVersion = Normalize-VersionValue (
                Get-Content $InstalledVersionFile -TotalCount 1 -ErrorAction SilentlyContinue | Select-Object -First 1
            )
        } else {
            $InstalledVersion = "unknown"
        }
        Write-Info "Installed version: $InstalledVersion"
    } else {
        Write-Info "No existing installation found at $InstallPath"
    }

    # -- Latest version --------------------------------------------------------
    Write-Info "Fetching latest release from GitHub ..."
    $LatestVer = Normalize-VersionValue (Get-LatestVersion)
    if (Test-KnownVersion $LatestVer) {
        Write-Ok "Latest release: $LatestVer"
    } else {
        Write-Warn "Could not determine the latest release version from GitHub."
        Write-Warn "The installer can still download the latest available binary, but the version check will be skipped."
    }

    if ($IsUpdate) {
        if ((Test-KnownVersion $LatestVer) -and ($InstalledVersion -eq $LatestVer)) {
            $forceUpdate = Prompt-YesNo "You already have the latest version ($LatestVer). Re-install / force update?" $false
            if (-not $forceUpdate) {
                Write-Info "Skipping download - nothing to update."
                Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
                return
            }
        } elseif (-not (Test-KnownVersion $LatestVer)) {
            $doUpdate = Prompt-YesNo "Could not verify the latest release version. Download the latest available binary anyway?" $false
            if (-not $doUpdate) {
                Write-Info "Update cancelled. Running post-install options only."
                Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
                return
            }
        } elseif (Test-KnownVersion $InstalledVersion) {
            $doUpdate = Prompt-YesNo "Update Game Sync from $InstalledVersion to $LatestVer?" $true
            if (-not $doUpdate) {
                Write-Info "Update cancelled. Running post-install options only."
                Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
                return
            }
        } else {
            $doUpdate = Prompt-YesNo "Could not determine the installed version, but the latest release is $LatestVer. Download and install it now?" $true
            if (-not $doUpdate) {
                Write-Info "Update cancelled. Running post-install options only."
                Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
                return
            }
        }
    }

    # -- Download --------------------------------------------------------------
    Write-Header "Step 4 - Download Game Sync"
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
    $VersionToWrite = if ($LatestVer -ne "unknown") { $LatestVer } else { "unknown" }
    Set-Content -Path (Get-InstalledVersionFile -InstallPath $InstallPath) -Value $VersionToWrite -Encoding UTF8
    Write-Ok "Game Sync installed to: $InstallPath"

    Post-Install -InstallPath $InstallPath -InstallDir $InstallDir -IsUpdate $IsUpdate
}

function Post-Install {
    param([string]$InstallPath, [string]$InstallDir, [bool]$IsUpdate)

    # -- Add to PATH -----------------------------------------------------------
    Write-Header "Step 5 - PATH"
    if (-not (PathContainsEntry -PathValue $env:Path -Entry $InstallDir)) {
        $addPath = Prompt-YesNo "Add $InstallDir to your user PATH? (lets you run 'game-sync' from any terminal)" $true
        if ($addPath) { Add-ToUserPath -Dir $InstallDir }
    } else {
        Write-Ok "$InstallDir is already in your PATH"
    }

    # -- Shortcuts -------------------------------------------------------------
    Write-Header "Step 6 - Shortcuts"

    $makeStartMenu = Prompt-YesNo "Create a Start Menu shortcut?" $true
    if ($makeStartMenu) { New-Shortcut -TargetPath $InstallPath | Out-Null }

    $makeDesktop = Prompt-YesNo "Create a Desktop shortcut?" $false
    if ($makeDesktop) { New-DesktopShortcut -TargetPath $InstallPath }

    # -- SSH / LAN sync --------------------------------------------------------
    Write-Header "Step 7 - LAN Sync (SSH)"
    Write-Info "Game Sync uses SSH to sync saves between machines on your network."
    Write-Info "To sync TO this Windows machine, OpenSSH Server must be enabled."
    Write-Warn "This requires administrator privileges."

    $sshStatus = Get-SshServerStatus
    $isAdmin = Test-IsAdmin

    if ($sshStatus.CapabilityQueryFailed -or $sshStatus.FirewallQueryFailed) {
        if (-not $isAdmin) {
            Write-Warn "Could not fully check OpenSSH Server status without administrator rights. The installer can still relaunch as admin if you choose to enable SSH."
        } else {
            Write-Warn "Could not fully check OpenSSH Server status automatically."
        }
    }

    if ($sshStatus.Ready) {
        Write-Ok "OpenSSH Server is already installed, running, set to start automatically, and allowed through the firewall on TCP 22."
    } else {
        if ($sshStatus.CapabilityQueryFailed) {
            Write-Info "OpenSSH Server install status could not be verified."
        } elseif ($sshStatus.Installed) {
            Write-Info "OpenSSH Server is installed."
        } else {
            Write-Info "OpenSSH Server is not installed."
        }

        if ($sshStatus.Running) {
            Write-Info "sshd service is running."
        } else {
            Write-Info "sshd service is not running."
        }

        if ($sshStatus.Automatic) {
            Write-Info "sshd is set to start automatically."
        } else {
            Write-Info "sshd is not set to start automatically."
        }

        if ($sshStatus.FirewallQueryFailed) {
            Write-Info "Firewall status for inbound TCP 22 could not be verified."
        } elseif ($sshStatus.FirewallAllows22) {
            Write-Info "Firewall already allows inbound TCP 22."
        } else {
            Write-Info "Firewall does not currently allow inbound TCP 22."
        }
    }

    $enableSsh = $false
    if (-not $sshStatus.Ready) {
        $enableSsh = Prompt-YesNo "Enable or finish configuring OpenSSH Server so other machines can sync to this PC?" $false
    }
    if ($enableSsh) {
        # Re-launch as admin if not already elevated
        if (-not $isAdmin) {
            if ([string]::IsNullOrWhiteSpace($PSCommandPath)) {
                Write-Warn "SSH setup requires administrator rights, but this installer was not started from a script file."
                Write-Warn "When run via an inline command (for example, irm ... | iex), PowerShell cannot relaunch this installer with -SSHOnly."
                Write-Warn "To enable OpenSSH Server, download this installer to a .ps1 file and run it as Administrator with -SSHOnly."
            } else {
                Write-Warn "Relaunching with administrator rights for SSH setup ..."
                $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -SSHOnly"
                Start-Process powershell -Verb RunAs -ArgumentList $args -Wait
            }
        } else {
            Enable-SshServer
        }
    }

    # -- rclone / Cloud sync ---------------------------------------------------
    Write-Header "Step 8 - Cloud Sync (Google Drive / Dropbox)"
    Write-Info "Cloud sync requires rclone - a free tool."
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

    # -- Summary ---------------------------------------------------------------
    Write-Host ""
    Write-Host "  $('=' * 44)" -ForegroundColor Green
    if ($IsUpdate) {
        Write-Host "  Game Sync updated successfully!" -ForegroundColor Green
    } else {
        Write-Host "  Game Sync installed successfully!" -ForegroundColor Green
    }
    Write-Host "  $('=' * 44)" -ForegroundColor Green
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

# -- Uninstall ----------------------------------------------------------------
function Uninstall-GameSync {
    Clear-Host
    Write-Host ""
    Write-Host "  +--------------------------------------+" -ForegroundColor Red
    Write-Host "  |       Game Sync  Uninstaller        |" -ForegroundColor Red
    Write-Host "  +--------------------------------------+" -ForegroundColor Red
    Write-Host ""

    # -- Find binary -----------------------------------------------------------
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "GameSync\game-sync.exe"),
        (Join-Path $env:PROGRAMFILES "GameSync\game-sync.exe"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "game-sync.exe")
    )
    $installDirCandidates = New-Object System.Collections.ArrayList
    Add-UniqueValue -List $installDirCandidates -Value (Join-Path $env:LOCALAPPDATA "GameSync")
    Add-UniqueValue -List $installDirCandidates -Value (Join-Path $env:PROGRAMFILES "GameSync")
    $foundPath = $null
    $custom = ""
    foreach ($c in $candidates) {
        if (Test-Path $c) { $foundPath = $c; break }
    }

    if ($foundPath) {
        Add-UniqueValue -List $installDirCandidates -Value (Split-Path $foundPath)
    }

    if (-not $foundPath) {
        Write-Warn "Game Sync not found in any standard location."
        Ask "Enter the full path to game-sync.exe (or press Enter to skip):"
        $custom = (Read-Host "  Path").Trim()
        if ($custom -ne "") {
            # Reject directories
            if ((Test-Path $custom) -and (Get-Item $custom -ErrorAction SilentlyContinue).PSIsContainer) {
                Write-Warn "'$custom' is a directory, not a file. Skipping binary removal."
                Add-UniqueValue -List $installDirCandidates -Value $custom
                $custom = ""
            # Warn on filename mismatch
            } elseif ([System.IO.Path]::GetFileName($custom) -ne "game-sync.exe") {
                Write-Warn "Warning: '$([System.IO.Path]::GetFileName($custom))' does not look like the Game Sync binary."
                Write-Warn "Expected filename: game-sync.exe"
                Write-Host "  Full path: $custom" -ForegroundColor Yellow
                Ask "Are you sure this is the correct file?"
                $pathConfirm = (Read-Host "  Type 'yes' to confirm, or press Enter to cancel").Trim().ToLower()
                if ($pathConfirm -ne "yes") {
                    Write-Info "Binary removal cancelled."
                    $custom = ""
                } elseif (-not (Test-Path $custom)) {
                    Write-Warn "File not found: $custom"
                    Add-UniqueValue -List $installDirCandidates -Value (Split-Path $custom)
                    $custom = ""
                } else {
                    Add-UniqueValue -List $installDirCandidates -Value (Split-Path $custom)
                }
            } elseif (-not (Test-Path $custom)) {
                Write-Warn "File not found: $custom"
                Add-UniqueValue -List $installDirCandidates -Value (Split-Path $custom)
                $custom = ""
            } else {
                Add-UniqueValue -List $installDirCandidates -Value (Split-Path $custom)
            }
        }
        if ($custom -ne "") { $foundPath = $custom }
    }

    # -- Remove binary ---------------------------------------------------------
    if ($foundPath) {
        Write-Host ""
        Write-Info "The following file will be deleted:"
        Write-Host "    $foundPath" -ForegroundColor White
        if (Test-Path "$foundPath.bak") {
            Write-Host "    $foundPath.bak  (backup)" -ForegroundColor White
        }
        $versionFile = Get-InstalledVersionFile -InstallPath $foundPath
        if (Test-Path $versionFile) {
            Write-Host "    $versionFile  (version file)" -ForegroundColor White
        }
        Ask "Confirm removal?"
        $removeBin = (Read-Host "  Type 'yes' to delete, or press Enter to skip").Trim().ToLower()
        if ($removeBin -eq "yes") {
            $removedBinary = Remove-PathSafely -Path $foundPath -Label $foundPath
            Remove-PathSafely -Path "$foundPath.bak" -Label "$foundPath.bak" | Out-Null
            Remove-PathSafely -Path $versionFile -Label $versionFile | Out-Null
            if ($removedBinary) {
                Write-Ok "Removed: $foundPath"
            }

            # Optionally remove now-empty install directory
            $dir = Split-Path $foundPath
            if ((Test-Path $dir) -and ((Get-ChildItem $dir -Force | Measure-Object).Count -eq 0)) {
                Write-Host ""
                Write-Warn "The directory containing the binary is now empty:"
                Write-Host "    $dir" -ForegroundColor White
                $knownDirs = @(
                    "$env:LOCALAPPDATA\GameSync",
                    "$env:PROGRAMFILES\GameSync"
                )
                if ($dir -notin $knownDirs) {
                    Write-Warn "This is a custom directory - deleting it will remove the entire folder."
                    Write-Warn "Make sure it does not contain other files you want to keep."
                }
                Ask "Delete this empty directory?"
                $rmDir = (Read-Host "  Type 'yes' to delete the directory, or press Enter to keep it").Trim().ToLower()
                if ($rmDir -eq "yes") {
                    if (Remove-PathSafely -Path $dir -Label $dir) {
                        Write-Ok "Removed empty directory: $dir"
                    }
                } else {
                    Write-Info "Directory kept: $dir"
                }
            }
        } else {
            Write-Info "Binary removal skipped."
        }
    } else {
        Write-Warn "No binary found - skipping binary removal."
    }

    # -- Remove rclone.exe ----------------------------------------------------
    $rcloneCandidates = @(
        $installDirCandidates |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { Join-Path $_ "rclone.exe" } |
            Select-Object -Unique
    )

    foreach ($rcloneInDir in $rcloneCandidates) {
        if (-not (Test-Path $rcloneInDir)) {
            continue
        }

        Write-Host ""
        Write-Info "The following file will be deleted:"
        Write-Host "    $rcloneInDir" -ForegroundColor White
        Ask "Also remove rclone.exe from the install folder?"
        $removeRclone = (Read-Host "  Type 'yes' to delete, or press Enter to keep").Trim().ToLower()
        if ($removeRclone -eq "yes") {
            if (Remove-PathSafely -Path $rcloneInDir -Label $rcloneInDir) {
                Write-Ok "Removed: $rcloneInDir"
            }
        } else {
            Write-Info "rclone.exe kept."
        }
    }

    # -- Remove Start Menu shortcut --------------------------------------------
    $startMenuLnk = Join-Path $ShortcutDir "Game Sync.lnk"
    if (Test-Path $startMenuLnk) {
        Write-Host ""
        Write-Info "The following file will be deleted:"
        Write-Host "    $startMenuLnk" -ForegroundColor White
        Ask "Remove Start Menu shortcut?"
        $removeStart = (Read-Host "  Type 'yes' to delete, or press Enter to keep").Trim().ToLower()
        if ($removeStart -eq "yes") {
            if (Remove-PathSafely -Path $startMenuLnk -Label $startMenuLnk) { Write-Ok "Removed: $startMenuLnk" }
        }
        else { Write-Info "Start Menu shortcut kept." }
    } else { Write-Info "No Start Menu shortcut found." }

    # -- Remove Desktop shortcut -----------------------------------------------
    $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Game Sync.lnk"
    if (Test-Path $desktopLnk) {
        Write-Host ""
        Write-Info "The following file will be deleted:"
        Write-Host "    $desktopLnk" -ForegroundColor White
        Ask "Remove Desktop shortcut?"
        $removeDesk = (Read-Host "  Type 'yes' to delete, or press Enter to keep").Trim().ToLower()
        if ($removeDesk -eq "yes") {
            if (Remove-PathSafely -Path $desktopLnk -Label $desktopLnk) { Write-Ok "Removed: $desktopLnk" }
        }
        else { Write-Info "Desktop shortcut kept." }
    } else { Write-Info "No Desktop shortcut found." }

    # -- Remove SSH firewall / service settings --------------------------------
    $sshFirewallRule = Get-OpenSshFirewallRule
    if ($null -ne $sshFirewallRule) {
        Write-Host ""
        Write-Info "Found the OpenSSH inbound firewall rule for TCP 22:"
        Write-Host "    $($sshFirewallRule.DisplayName) [$($sshFirewallRule.Name)]" -ForegroundColor White
        $removeFirewallRule = Prompt-YesNo "Remove the OpenSSH firewall rule for inbound TCP 22?" $false
        if ($removeFirewallRule) {
            if (-not (Test-IsAdmin)) {
                Write-Warn "Removing firewall rules requires administrator rights. Re-run the uninstaller as Administrator if you want to remove it."
            } else {
                try {
                    Remove-NetFirewallRule -Name $sshFirewallRule.Name -ErrorAction Stop | Out-Null
                    Write-Ok "Removed firewall rule: $($sshFirewallRule.DisplayName)"
                } catch {
                    Write-Warn "Could not remove firewall rule '$($sshFirewallRule.Name)': $($_.Exception.Message)"
                }
            }
        } else {
            Write-Info "Firewall rule kept."
        }
    }

    $sshService = Get-Service sshd -ErrorAction SilentlyContinue
    if ($null -ne $sshService) {
        Write-Host ""
        Write-Info "OpenSSH Server service settings:"
        Write-Host "    Service: sshd" -ForegroundColor White
        Write-Host "    Status: $($sshService.Status)" -ForegroundColor White
        Write-Host "    Startup: $($sshService.StartType)" -ForegroundColor White
        $removeSshSettings = Prompt-YesNo "Disable the sshd service and stop it if it is running?" $false
        if ($removeSshSettings) {
            if (-not (Test-IsAdmin)) {
                Write-Warn "Changing sshd service settings requires administrator rights. Re-run the uninstaller as Administrator if you want to disable it."
            } else {
                try {
                    if ($sshService.Status -eq 'Running') {
                        Stop-Service sshd -Force -ErrorAction Stop
                        Write-Ok "sshd service stopped."
                    }
                    Set-Service -Name sshd -StartupType Disabled -ErrorAction Stop
                    Write-Ok "sshd service disabled."
                } catch {
                    Write-Warn "Could not update sshd service settings: $($_.Exception.Message)"
                }
            }
        } else {
            Write-Info "sshd service settings left unchanged."
        }
    }

    # -- Remove settings file --------------------------------------------------
    $settings = Join-Path $env:APPDATA "game_sync_settings.json"
    if (Test-Path $settings) {
        Write-Host ""
        Write-Info "The following file will be deleted (contains your sync paths and preferences):"
        Write-Host "    $settings" -ForegroundColor White
        Ask "Remove saved settings? (default: keep)"
        $removeSettings = (Read-Host "  Type 'yes' to delete, or press Enter to keep").Trim().ToLower()
        if ($removeSettings -eq "yes") {
            if (Remove-PathSafely -Path $settings -Label $settings) { Write-Ok "Removed: $settings" }
        }
        else { Write-Info "Settings kept at $settings" }
    }

    # -- Remove from user PATH (exact entry match only) ------------------------
    if ($foundPath) {
        $installDir = Split-Path $foundPath
        $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (PathContainsEntry -PathValue $currentPath -Entry $installDir) {
            $pathEntries = Get-PathEntries $currentPath
            Write-Host ""
            Write-Info "Found this user PATH entry added by the installer:"
            Write-Host "    $installDir" -ForegroundColor White
            Ask "Remove it from user PATH?"
            $removePath = (Read-Host "  Type 'yes' to remove, or press Enter to keep").Trim().ToLower()
            if ($removePath -eq "yes") {
                $newPath = ($pathEntries | Where-Object { $_ -ne $installDir }) -join ';'
                [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
                Write-Ok "Removed $installDir from user PATH"
            } else {
                Write-Info "PATH left unchanged."
            }
        }
    }

    Write-Host ""
    Write-Host "  $('=' * 44)" -ForegroundColor Green
    Write-Host "  Game Sync uninstalled." -ForegroundColor Green
    Write-Host "  $('=' * 44)" -ForegroundColor Green
    Write-Host ""
    Read-Host "  Press Enter to exit"
}

# -- Entry point --------------------------------------------------------------
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
