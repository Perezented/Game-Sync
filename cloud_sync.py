import os
import platform
import subprocess
import shutil
import tempfile
import zipfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

# Suppress console-window flicker when running as a Windows EXE
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── rclone handles GDrive + Dropbox without developer accounts ───────────────
# Install from https://rclone.org/install/


def rclone_is_available() -> bool:
    return bool(shutil.which("rclone"))


RCLONE_AVAILABLE = rclone_is_available()


class RcloneSync:
    """Cloud sync for Google Drive and Dropbox via rclone.

    No developer accounts needed — rclone uses its own bundled OAuth credentials.
    Users authorize once via their browser (Google/Dropbox account login).
    Install rclone from: https://rclone.org/install/
    """

    PROVIDER_TYPE = {"gdrive": "drive", "dropbox": "dropbox"}

    def __init__(self, provider: str, token_json: str = ""):
        self.provider = provider  # "gdrive" or "dropbox"
        self.token_json = token_json  # JSON string of the rclone token

    def is_authenticated(self) -> bool:
        return bool(self.token_json)

    def _config_path(self) -> Path:
        if platform.system() == "Windows":
            cfg_dir = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / "game-sync-tool"
        else:
            cfg_dir = Path.home() / ".config" / "game-sync-tool"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return cfg_dir / f"rclone_{self.provider}.conf"

    def _write_config(self) -> Path:
        cfg = self._config_path()
        rtype = self.PROVIDER_TYPE.get(self.provider, self.provider)
        cfg.write_text(
            f"[{self.provider}]\ntype = {rtype}\ntoken = {self.token_json}\n",
            encoding="utf-8",
        )
        if platform.system() != "Windows":
            try:
                os.chmod(cfg, 0o600)
            except OSError:
                pass
        return cfg

    def _run(self, cmd, on_line=None, on_proc=None, cancelled=None):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
        if on_proc:
            on_proc(proc)
        for line in proc.stdout:
            line = line.rstrip()
            if line and on_line:
                on_line(line)
            if cancelled and cancelled():
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.wait()
                return
        proc.wait()
        if proc.returncode != 0 and not (cancelled and cancelled()):
            raise RuntimeError(f"rclone exited with code {proc.returncode}")

    def _ensure_rclone_installed(self):
        if not rclone_is_available():
            raise RuntimeError(
                "rclone is not installed. Install it from https://rclone.org/install/."
            )

    @staticmethod
    def _zip_folder(local_path: str, zip_path: str, on_line=None) -> None:
        """Zip a directory into zip_path, preserving the top-level folder name."""
        src = Path(local_path)
        parent = src.parent
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(src.rglob("*")):
                if file.is_file():
                    zf.write(file, file.relative_to(parent))
        size_kib = Path(zip_path).stat().st_size // 1024
        if on_line:
            on_line(f"[zip] {Path(zip_path).name} ready ({size_kib:,} KiB)")

    @staticmethod
    def _unzip_to(zip_path: str, dest_dir: str, on_line=None) -> None:
        """Extract zip_path into dest_dir (overwrites existing files)."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest)
        if on_line:
            on_line(f"[unzip] Extracted to {dest_dir}")

    def upload(
        self, local_path, cloud_folder, on_line=None, on_proc=None, cancelled=None,
        zip_transfer: bool = False,
    ):
        self._ensure_rclone_installed()
        cfg = self._write_config()
        local_path = str(
            Path(
                str(local_path)
                .replace("%USERPROFILE%", str(Path.home()))
                .replace("%APPDATA%", str(Path.home() / "AppData" / "Roaming"))
            )
            .expanduser()
            .resolve()
        )
        remote = f"{self.provider}:{cloud_folder.lstrip('/')}"

        if zip_transfer and Path(local_path).is_dir():
            zip_name = Path(local_path).name + ".zip"
            if on_line:
                on_line(f"[zip] Compressing {Path(local_path).name}…")
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = str(Path(tmpdir) / zip_name)
                self._zip_folder(local_path, zip_path, on_line)
                if on_line:
                    on_line(f"[rclone upload] {zip_path!r}  →  {remote!r}")
                self._run(
                    [
                        "rclone", "copyto", "--config", str(cfg),
                        zip_path,
                        f"{remote.rstrip('/')}/{zip_name}",
                        "-v", "--stats-one-line-date",
                    ],
                    on_line, on_proc, cancelled,
                )
            return

        if on_line:
            on_line(f"[rclone upload] {local_path!r}  →  {remote!r}")
        self._run(
            [
                "rclone",
                "copy",
                "--config",
                str(cfg),
                str(local_path),
                remote,
                "-v",
                "--stats-one-line-date",
            ],
            on_line,
            on_proc,
            cancelled,
        )

    def download(
        self, cloud_folder, local_path, on_line=None, on_proc=None, cancelled=None,
        zip_transfer: bool = False,
    ):
        self._ensure_rclone_installed()
        cfg = self._write_config()
        local_path = str(
            Path(
                str(local_path)
                .replace("%USERPROFILE%", str(Path.home()))
                .replace("%APPDATA%", str(Path.home() / "AppData" / "Roaming"))
            )
            .expanduser()
            .resolve()
        )
        remote = f"{self.provider}:{cloud_folder.lstrip('/')}"

        if zip_transfer:
            zip_name = Path(local_path).name + ".zip"
            remote_zip = f"{remote.rstrip('/')}/{zip_name}"
            if on_line:
                on_line(f"[rclone download] {remote_zip!r}  →  (temp)")
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_zip = str(Path(tmpdir) / zip_name)
                self._run(
                    [
                        "rclone", "copyto", "--config", str(cfg),
                        remote_zip, tmp_zip,
                        "-v", "--stats-one-line-date",
                    ],
                    on_line, on_proc, cancelled,
                )
                if cancelled and cancelled():
                    return
                if on_line:
                    on_line(f"[unzip] Extracting {zip_name}…")
                self._unzip_to(tmp_zip, str(Path(local_path).parent), on_line)
            return

        Path(local_path).mkdir(parents=True, exist_ok=True)
        if on_line:
            on_line(f"[rclone download] {remote!r}  →  {local_path!r}")
        self._run(
            [
                "rclone",
                "copy",
                "--config",
                str(cfg),
                remote,
                str(local_path),
                "-v",
                "--stats-one-line-date",
            ],
            on_line,
            on_proc,
            cancelled,
        )


# ── Background thread for cloud operations ────────────────────────────────────


class CloudWorkerThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, operation: str, sync_obj, local_path: str, cloud_folder: str,
                 zip_transfer: bool = False, zip_lan: bool = False):
        super().__init__()
        self.operation = operation  # "upload" or "download"
        self.sync_obj = sync_obj
        self.local_path = local_path
        self.cloud_folder = cloud_folder
        self.zip_transfer = zip_transfer
        self.zip_lan = zip_lan
        self._cancelled = False
        self._proc = None  # subprocess.Popen reference for LocalNetworkSync rsync

    def cancel(self):
        self._cancelled = True
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def _store_proc(self, proc):
        self._proc = proc

    def run(self):
        if self._cancelled:
            self.finished.emit(False, "Cancelled.")
            return
        try:
            # ── LocalNetworkSync: use push_path / pull_path for streaming output,
            #    cancel support and rsync --update semantics (same as Direct Sync).
            if hasattr(self.sync_obj, "push_path"):
                lns = self.sync_obj
                # Remote path = remote_base / cloud_folder  (mirrors upload() logic)
                remote_path = (
                    f"{lns.remote_base.rstrip('/')}/{self.cloud_folder.lstrip('/')}"
                )
                if self.operation == "upload":
                    self.progress.emit(f"── Pushing to {lns.ip}:{remote_path} ──")
                    lns.push_path(
                        self.local_path,
                        remote_path,
                        on_line=self.progress.emit,
                        on_proc=self._store_proc,
                        cancelled=lambda: self._cancelled,
                        zip_transfer=self.zip_lan,
                    )
                    if self._cancelled:
                        self.finished.emit(False, "Cancelled.")
                    else:
                        self.finished.emit(True, "Upload complete.")
                else:
                    self.progress.emit(f"── Pulling from {lns.ip}:{remote_path} ──")
                    lns.pull_path(
                        remote_path,
                        self.local_path,
                        on_line=self.progress.emit,
                        on_proc=self._store_proc,
                        cancelled=lambda: self._cancelled,
                        zip_transfer=self.zip_lan,
                    )
                    if self._cancelled:
                        self.finished.emit(False, "Cancelled.")
                    else:
                        self.finished.emit(True, "Download complete.")
                return

            # ── RcloneSync (GDrive/Dropbox): streaming output + cancel support ──
            self.progress.emit(f"{self.operation.title()}ing via cloud…")
            if self.operation == "upload":
                self.sync_obj.upload(
                    self.local_path,
                    self.cloud_folder,
                    on_line=self.progress.emit,
                    on_proc=self._store_proc,
                    cancelled=lambda: self._cancelled,
                    zip_transfer=self.zip_transfer,
                )
                if self._cancelled:
                    self.finished.emit(False, "Cancelled.")
                else:
                    self.finished.emit(True, "Upload complete.")
            else:
                self.sync_obj.download(
                    self.cloud_folder,
                    self.local_path,
                    on_line=self.progress.emit,
                    on_proc=self._store_proc,
                    cancelled=lambda: self._cancelled,
                    zip_transfer=self.zip_transfer,
                )
                if self._cancelled:
                    self.finished.emit(False, "Cancelled.")
                else:
                    self.finished.emit(True, "Download complete.")
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.finished.emit(False, "Cancelled." if self._cancelled else str(exc))
