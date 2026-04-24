import os
import sys
import shutil
from pathlib import Path

def expand_path(path_str):
    # Handle Windows-style %USERPROFILE% manually
    if "%USERPROFILE%" in path_str:
        path_str = path_str.replace("%USERPROFILE%", os.path.expanduser("~"))
        path_str = path_str.replace("\\", "/")
    return Path(path_str).expanduser()

def sync_saves(source_raw, destination_raw):
    source_path = expand_path(source_raw)
    destination_path = expand_path(destination_raw)

    print(f"\n--- Syncing ---")
    print(f"Source:      {source_path}")
    print(f"Destination: {destination_path}")

    if not source_path.exists():
        print(f"Error: Source path '{source_path}' does not exist.")
        return

    os.makedirs(destination_path, exist_ok=True)

    try:
        if source_path.is_dir():
            # Prototype mode: clean sync
            for item in os.listdir(source_path):
                s = source_path / item
                d = destination_path / item
                if s.is_dir():
                    if d.exists():
                        shutil.rmtree(d)
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)
            print("Sync completed successfully.")
        else:
            shutil.copy2(source_path, destination_path)
            print("File sync completed successfully.")

    except Exception as e:
        print(f"An error occurred during sync: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 sync_engine.py <source> <destination>")
    else:
        sync_saves(sys.argv[1], sys.argv[2])