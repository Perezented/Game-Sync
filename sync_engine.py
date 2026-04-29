import os
import sys
import shutil
from pathlib import Path


def expand_path(path_str):
    """Expand environment variables and ~ in a path string and return a Path."""
    path_str = path_str.replace("%APPDATA%", str(Path.home() / "AppData" / "Roaming"))
    path_str = path_str.replace("%USERPROFILE%", str(Path.home()))
    path_str = path_str.replace("\\", "/")
    return Path(path_str).expanduser()


def _should_copy(src: Path, dst: Path) -> bool:
    """Return True if src should overwrite dst.

    Mirrors rsync --update: skip the copy when the destination exists and its
    modification time is greater than or equal to the source's.  A 1-second
    tolerance is applied to absorb FAT/exFAT filesystem rounding and minor
    clock-skew between machines.
    """
    if not dst.exists():
        return True
    src_mtime = src.stat().st_mtime
    dst_mtime = dst.stat().st_mtime
    return src_mtime > dst_mtime + 1.0


def _sync_dir(source: Path, destination: Path, counters: dict):
    """Recursively sync source directory into destination (mtime-aware)."""
    destination.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        s = item
        d = destination / item.name

        if s.is_dir():
            _sync_dir(s, d, counters)
        else:
            if _should_copy(s, d):
                shutil.copy2(s, d)
                counters["copied"] += 1
                print(f"  copied : {s.name}")
            else:
                counters["skipped"] += 1
                print(f"  skipped: {s.name}  (destination is up-to-date)")


def sync_saves(source_raw, destination_raw):
    source_path = expand_path(source_raw)
    destination_path = expand_path(destination_raw)

    print(f"\n--- Syncing ---")
    print(f"Source:      {source_path}")
    print(f"Destination: {destination_path}")

    if not source_path.exists():
        print(f"Error: Source path '{source_path}' does not exist.")
        return

    try:
        counters = {"copied": 0, "skipped": 0}

        if source_path.is_dir():
            _sync_dir(source_path, destination_path, counters)
            total = counters["copied"] + counters["skipped"]
            print(
                f"\nSync complete — "
                f"{counters['copied']} / {total} file(s) copied "
                f"({counters['skipped']} already up-to-date)."
            )
        else:
            destination_path.mkdir(parents=True, exist_ok=True)
            dst_file = destination_path / source_path.name if destination_path.is_dir() else destination_path
            if _should_copy(source_path, dst_file):
                shutil.copy2(source_path, dst_file)
                print("File sync complete — 1 file copied.")
            else:
                print("File sync complete — destination is already up-to-date, nothing copied.")

    except Exception as e:
        print(f"An error occurred during sync: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 sync_engine.py <source> <destination>")
    else:
        sync_saves(sys.argv[1], sys.argv[2])