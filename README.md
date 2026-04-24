# Game Sync App

A small game sync utility that started (and can be expanded for more games without Steam Cloud Saves) for Project Zomboid that helps keep save files in sync across machines and platforms.

## Overview

This project includes a simple GUI application and a sync engine for copying or mirroring game save folders between local and remote destinations.

## Contents

- `gui_app.py` - PyQt6-based user interface for selecting games, sync direction, and triggering sync.
- `sync_engine.py` - Core path expansion and sync logic for copying files or directories.
- `config.json` - Example sync entries for Windows/Linux save locations.
- `game_defaults.json` - Default save paths for supported games and platforms.
- `mock_data/` - Example folder structure used for testing or development.

## Requirements

- Python 3.10+ (or compatible)
- PyQt6

## Installation

1. Create and activate a Python virtual environment inside the project folder:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install PyQt6
```

## Running the App

Run the GUI application with:

```bash
python gui_app.py
```

## Sync Engine Usage

The sync engine can also be used from the command line:

```bash
python sync_engine.py "<source>" "<destination>"
```

Example:

```bash
python sync_engine.py "%USERPROFILE%\\Zomboid\\Saves" "~/Zomboid/Saves"
```

## Configuration

- `config.json` defines sync entries for local and destination paths.
- `game_defaults.json` stores default save paths by game and platform.

### Path Expansion

The sync engine supports Windows-style `%USERPROFILE%` expansion and `~` home directory expansion.

## Notes

- The current GUI is a prototype with placeholder LAN machine discovery and is intended for local development and testing.
- The sync logic currently performs a directory-level copy and replaces directories with fresh copies.

## License

This repository does not include a specified license file. Add a license if you plan to share or publish the project.
