# Game Sync - Release Notes

## v1.1.1 - April 30, 2026

This update focuses on installer polish, version tracking, and clearer setup guidance.

### Highlights

- Added app version tracking for both Windows and Linux installs
- Improved installer update checks so existing installs can be identified more reliably
- Enhanced SSH detection and setup feedback in the installers
- Added fresh screenshots to the README for Windows and Linux / Steam Deck

### What's New

#### App versioning

- Added a dedicated app version module
- The app now supports `--version` and `-V` to print the current version and exit
- Installers now save an adjacent version file so updates can compare the installed version more reliably

#### Installer improvements

- Windows installer now has a cleaner update flow with explicit installed-version tracking
- Linux installer now detects more SSH service names and binaries, including common `ssh`, `sshd`, and `dropbear` setups
- Linux installer gives better install hints when an SSH server is not yet installed
- Uninstall flows now also remove the saved version-tracking file

#### Documentation updates

- Added a Windows app screenshot to the README
- Added a Linux / Steam Deck app screenshot to the README
- Improved screenshot layout in the README with a table-based presentation

### Changed Files

- `app_version.py` added for shared version reporting
- `game-sync.py` updated to expose version output from the command line
- `install.ps1` updated with version tracking and improved installer logic
- `install.sh` updated with version tracking and stronger SSH detection
- `README.md` updated with new screenshots and layout improvements

### Notes

- This is primarily a quality-of-life and packaging update
- No major sync workflow changes were introduced in this compare range
