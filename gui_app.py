import sys
import os
import socket
import ipaddress
import concurrent.futures
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QPushButton,
    QFileDialog,
    QLabel,
    QLineEdit,
    QComboBox,
    QProgressBar,
    QStyle,
    QSizePolicy,
    QCheckBox,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QFrame,
    QScrollArea,
    QInputDialog,
    QSplitter,
    QPlainTextEdit,
    QTabWidget,
    QSpinBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
import shlex
import json
import platform
import webbrowser
import urllib.parse

import shutil

# ── rclone handles GDrive + Dropbox without developer accounts ───────────────
# Install from https://rclone.org/install/
RCLONE_AVAILABLE = bool(shutil.which("rclone"))

try:
    import paramiko

    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


class NetworkScanner(QThread):
    """Scans the local /24 subnet for live hosts and guesses their OS."""

    scan_complete = pyqtSignal(list)  # list of (ip, os_type, label, mac)
    scan_status = pyqtSignal(str)  # progress messages

    # Port -> OS hint, checked in priority order
    OS_PORTS = [
        (445, "Windows"),  # SMB
        (3389, "Windows"),  # RDP
        (22, "Linux"),  # SSH
    ]

    def run(self):
        self.scan_status.emit("Scanning network…")
        local_ip = self._get_local_ip()
        if not local_ip:
            self.scan_status.emit("Could not determine local IP.")
            self.scan_complete.emit([])
            return

        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        hosts = [str(h) for h in network.hosts()]

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
            futures = {executor.submit(self._probe_host, ip): ip for ip in hosts}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        results.sort(key=lambda x: list(map(int, x[0].split("."))))
        self.scan_status.emit(f"Scan complete — {len(results)} host(s) found.")
        self.scan_complete.emit(results)

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _probe_host(self, ip):
        os_type = "Unknown"
        alive = False

        for port, hint in self.OS_PORTS:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.4)
                if s.connect_ex((ip, port)) == 0:
                    alive = True
                    os_type = hint
                    s.close()
                    break
                s.close()
            except Exception:
                pass

        if not alive:
            return None

        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            hostname = ip

        mac = self._get_mac_for_ip(ip)
        label = f"{ip}  ({hostname})  [{os_type}]"
        return (ip, os_type, label, mac)

    def _get_mac_for_ip(self, ip):
        try:
            result = subprocess.run(
                ["ip", "neigh", "show", ip],
                capture_output=True,
                text=True,
                check=False,
            )
            parts = result.stdout.strip().split()
            if "lladdr" in parts:
                return parts[parts.index("lladdr") + 1].lower()
        except Exception:
            pass
        return ""


# ── Google Drive helper ───────────────────────────────────────────────────────


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
        return cfg

    def _run(self, cmd, on_line=None, on_proc=None, cancelled=None):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if on_proc:
            on_proc(proc)
        for line in proc.stdout:
            line = line.rstrip()
            if line and on_line:
                on_line(line)
            if cancelled and cancelled():
                proc.kill()
                return
        proc.wait()
        if proc.returncode != 0 and not (cancelled and cancelled()):
            raise RuntimeError(f"rclone exited with code {proc.returncode}")

    def upload(
        self, local_path, cloud_folder, on_line=None, on_proc=None, cancelled=None
    ):
        cfg = self._write_config()
        remote = f"{self.provider}:{cloud_folder.lstrip('/')}"
        self._run(
            [
                "rclone",
                "copy",
                "--config",
                str(cfg),
                str(local_path),
                remote,
                "--stats-one-line-date",
            ],
            on_line,
            on_proc,
            cancelled,
        )

    def download(
        self, cloud_folder, local_path, on_line=None, on_proc=None, cancelled=None
    ):
        cfg = self._write_config()
        remote = f"{self.provider}:{cloud_folder.lstrip('/')}"
        Path(local_path).mkdir(parents=True, exist_ok=True)
        self._run(
            [
                "rclone",
                "copy",
                "--config",
                str(cfg),
                remote,
                str(local_path),
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

    def __init__(self, operation: str, sync_obj, local_path: str, cloud_folder: str):
        super().__init__()
        self.operation = operation  # "upload" or "download"
        self.sync_obj = sync_obj
        self.local_path = local_path
        self.cloud_folder = cloud_folder
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
                )
                if self._cancelled:
                    self.finished.emit(False, "Cancelled.")
                else:
                    self.finished.emit(True, "Download complete.")
        except Exception as exc:
            import traceback

            traceback.print_exc()
            self.finished.emit(False, "Cancelled." if self._cancelled else str(exc))


# ── Local network machine ("private cloud") helper ────────────────────────────


class LocalNetworkSync:
    """Push/pull saves to/from any SSH-accessible machine on the LAN (e.g. a Pi)."""

    def __init__(
        self,
        ip: str,
        username: str,
        remote_base: str,
        ssh_port: int = 22,
        ssh_key: str = "",
        ssh_password: str = "",
    ):
        self.ip = ip
        self.username = username
        self.remote_base = remote_base.rstrip("/")
        self.ssh_port = ssh_port
        self.ssh_key = ssh_key  # path to private key, optional
        self.ssh_password = ssh_password

    def is_authenticated(self) -> bool:
        return bool(self.ip and self.username)

    def _ssh_opts(self, batch_mode: bool = False) -> list[str]:
        opts = [
            "-p",
            str(self.ssh_port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "PreferredAuthentications=publickey,password",
        ]
        if batch_mode:
            opts += ["-o", "BatchMode=yes"]
        if self.ssh_key:
            opts += ["-i", self.ssh_key]
        return opts

    def _has_sshpass(self) -> bool:
        return subprocess.run(["which", "sshpass"], capture_output=True).returncode == 0

    def _with_password(self, cmd: list[str]) -> list[str]:
        if self.ssh_password:
            if not self._has_sshpass():
                raise RuntimeError(
                    "sshpass is required for password authentication. "
                    "Install sshpass or use an SSH key."
                )
            return ["sshpass", "-p", self.ssh_password] + cmd
        return cmd

    def _remote_addr(self, subpath: str = "") -> str:
        return f"{self.username}@{self.ip}:{self.remote_base}/{subpath.lstrip('/')}"

    def test_connection(self) -> tuple[bool, str]:
        if PARAMIKO_AVAILABLE:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                connect_kwargs = {
                    "hostname": self.ip,
                    "port": self.ssh_port,
                    "username": self.username,
                    "timeout": 8,
                    "auth_timeout": 8,
                    "look_for_keys": False,
                    "allow_agent": False,
                }
                if self.ssh_key:
                    connect_kwargs["key_filename"] = self.ssh_key
                if self.ssh_password:
                    connect_kwargs["password"] = self.ssh_password
                client.connect(**connect_kwargs)
                client.close()
                return True, "Connection successful."
            except Exception as exc:
                return False, str(exc)

        try:
            batch_mode = not bool(self.ssh_password)
            cmd = self._with_password(
                ["ssh"]
                + self._ssh_opts(batch_mode=batch_mode)
                + [f"{self.username}@{self.ip}", "echo OK"]
            )
        except Exception as exc:
            return False, str(exc)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=8,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return True, "Connection successful."
            return False, result.stderr.strip() or "Connection failed."
        except Exception as exc:
            return False, str(exc)

    # ── paramiko SFTP helpers ──────────────────────────────────────────────────

    def _sftp_client(self):
        """Return a connected (SSHClient, SFTPClient) pair using the stored password."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.ip,
            port=self.ssh_port,
            username=self.username,
            password=self.ssh_password or None,
            key_filename=self.ssh_key if self.ssh_key else None,
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
            auth_timeout=10,
        )
        return client, client.open_sftp()

    def _sftp_mkdir_p(self, sftp, remote_dir: str):
        """Recursively create remote directories via SFTP (like mkdir -p)."""
        parts = remote_dir.replace("\\", "/").split("/")
        path = ""
        for part in parts:
            if not part:
                path = "/"
                continue
            path = (
                f"{path}/{part}"
                if path and path != "/"
                else f"/{part}" if path == "/" else part
            )
            try:
                sftp.stat(path)
            except OSError:
                # Directory doesn't exist (or stat failed) — try to create it.
                # Ignore EEXIST in case of a race or if the server reports the
                # parent-not-found as PermissionError instead of ENOENT.
                try:
                    sftp.mkdir(path)
                except OSError:
                    pass  # already exists or skip — stat on next iteration catches real failures

    def _sftp_put_recursive(
        self, sftp, local: Path, remote: str, on_line=None, cancelled=None
    ):
        """Upload local file or directory tree to remote path via SFTP.

        Mirrors rsync --update: skips files where the destination is newer.
        """
        if cancelled and cancelled():
            raise RuntimeError("Cancelled.")
        self._sftp_mkdir_p(sftp, remote)
        if local.is_file():
            dest = f"{remote}/{local.name}"
            skip = False
            try:
                remote_attr = sftp.stat(dest)
                if remote_attr.st_mtime >= local.stat().st_mtime:
                    skip = True
            except FileNotFoundError:
                pass
            if skip:
                if on_line:
                    on_line(f"  skipping {local.name} (destination is newer or same)")
            else:
                if on_line:
                    on_line(f"  sending {local.name}")
                sftp.put(str(local), dest)
        else:
            for item in local.iterdir():
                if cancelled and cancelled():
                    raise RuntimeError("Cancelled.")
                r_sub = f"{remote}/{item.name}"
                if item.is_dir():
                    self._sftp_mkdir_p(sftp, r_sub)
                    self._sftp_put_recursive(
                        sftp, item, r_sub, on_line=on_line, cancelled=cancelled
                    )
                else:
                    skip = False
                    try:
                        remote_attr = sftp.stat(r_sub)
                        if remote_attr.st_mtime >= item.stat().st_mtime:
                            skip = True
                    except FileNotFoundError:
                        pass
                    if skip:
                        if on_line:
                            on_line(
                                f"  skipping {item.name} (destination is newer or same)"
                            )
                    else:
                        if on_line:
                            on_line(f"  sending {item.name}")
                        sftp.put(str(item), r_sub)

    def _sftp_get_recursive(
        self, sftp, remote: str, local: Path, on_line=None, cancelled=None
    ):
        """Download remote directory tree to local path via SFTP.

        Mirrors rsync --update: skips files where the local copy is newer or same.
        """
        import stat as _stat

        if cancelled and cancelled():
            raise RuntimeError("Cancelled.")
        local.mkdir(parents=True, exist_ok=True)
        for entry in sftp.listdir_attr(remote):
            if cancelled and cancelled():
                raise RuntimeError("Cancelled.")
            r_path = f"{remote}/{entry.filename}"
            l_path = local / entry.filename
            if _stat.S_ISDIR(entry.st_mode):
                self._sftp_get_recursive(
                    sftp, r_path, l_path, on_line=on_line, cancelled=cancelled
                )
            else:
                skip = False
                if l_path.exists():
                    if l_path.stat().st_mtime >= entry.st_mtime:
                        skip = True
                if skip:
                    if on_line:
                        on_line(f"  skipping {r_path} (local is newer or same)")
                else:
                    if on_line:
                        # Show the remote path relative to the remote base for clarity
                        on_line(f"  receiving {r_path}")
                    sftp.get(r_path, str(l_path))

    # ── public transfer methods ────────────────────────────────────────────────

    def upload(self, local_path: str | Path, cloud_folder: str):
        lp = Path(local_path)
        if self.ssh_password:
            if not PARAMIKO_AVAILABLE:
                raise RuntimeError(
                    "paramiko is required for password-based SSH transfers. "
                    "Run: pip install paramiko"
                )
            client, sftp = self._sftp_client()
            try:
                remote = f"{self.remote_base}/{cloud_folder.lstrip('/')}"
                self._sftp_put_recursive(sftp, lp, remote)
            finally:
                sftp.close()
                client.close()
            return

        # Key-based auth: rsync preferred, scp fallback
        dest = self._remote_addr(cloud_folder)
        rsync = subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        if rsync:
            ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
            cmd = [
                "rsync",
                "-az",
                "--mkpath",
                "-e",
                ssh_cmd,
                str(lp) + ("/" if lp.is_dir() else ""),
                dest,
            ]
        else:
            cmd = ["scp"] + self._ssh_opts(batch_mode=True) + ["-r", str(lp), dest]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"Transfer failed (exit {result.returncode})"
            )

    def download(self, cloud_folder: str, local_path: str | Path):
        lp = Path(local_path)
        if self.ssh_password:
            if not PARAMIKO_AVAILABLE:
                raise RuntimeError(
                    "paramiko is required for password-based SSH transfers. "
                    "Run: pip install paramiko"
                )
            client, sftp = self._sftp_client()
            try:
                remote = f"{self.remote_base}/{cloud_folder.lstrip('/')}"
                self._sftp_get_recursive(sftp, remote, lp)
            finally:
                sftp.close()
                client.close()
            return

        # Key-based auth: rsync preferred, scp fallback
        lp.mkdir(parents=True, exist_ok=True)
        src = self._remote_addr(cloud_folder)
        rsync = subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        if rsync:
            ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
            cmd = ["rsync", "-az", "-e", ssh_cmd, src + "/", str(lp)]
        else:
            cmd = ["scp"] + self._ssh_opts(batch_mode=True) + ["-r", src, str(lp)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip() or f"Transfer failed (exit {result.returncode})"
            )

    # ── Direct machine-to-machine sync (absolute paths, rsync --update) ────────

    def _expand_remote_path(self, client, path: str) -> str:
        """Expand ~ / $HOME / %USERPROFILE% in a remote path (Linux or Windows remote)."""
        import re as _re_exp

        # Fast-path: nothing to expand
        if not path.startswith("~") and "$" not in path and "%" not in path:
            return path

        # ── Step 1: resolve the remote home directory ─────────────────────────
        # Try POSIX $HOME first (works on Linux/macOS SSH servers).
        _, stdout, _ = client.exec_command("echo $HOME")
        home = stdout.read().decode().strip()

        # If $HOME came back unexpanded (Windows cmd.exe echoes it literally),
        # try %USERPROFILE% instead.
        if not home or home == "$HOME":
            _, stdout, _ = client.exec_command("echo %USERPROFILE%")
            home = stdout.read().decode().strip()

        # ── Step 2: substitute known patterns ────────────────────────────────
        if home and home not in ("$HOME", "%USERPROFILE%"):
            # Use a lambda replacement so backslashes in `home` (Windows paths)
            # are never interpreted as regex escape sequences.
            _repl = lambda m: home  # noqa: E731
            # $HOME / ${HOME}
            path = _re_exp.sub(r"\$\{?HOME\}?", _repl, path)
            # %USERPROFILE%
            path = _re_exp.sub(r"%USERPROFILE%", _repl, path, flags=_re_exp.IGNORECASE)
            # Leading ~  (must come last so ~/foo still works after $HOME removal)
            if path.startswith("~"):
                path = home + path[1:]

        return path

    def push_path(
        self,
        local_path: str | Path,
        remote_path: str,
        on_line=None,
        on_proc=None,
        cancelled=None,
    ):
        """Push local_path to absolute remote_path on the remote machine."""

        def _log(msg):
            print(msg)
            if on_line:
                on_line(msg)

        lp = Path(local_path).expanduser()
        _log(f"[push] local={lp}  remote={remote_path}")
        if not lp.exists():
            raise FileNotFoundError(
                f"Local source path does not exist: {lp}\n"
                f"Check the Source Path field in the UI."
            )
        has_rsync = (
            subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        )
        if self.ssh_password or not has_rsync:
            if not PARAMIKO_AVAILABLE:
                raise RuntimeError("paramiko is required. Run: pip install paramiko")
            client, sftp = self._sftp_client()
            try:
                remote_path = self._expand_remote_path(client, remote_path)
                _log(f"[push] SFTP → {remote_path}")
                self._sftp_mkdir_p(sftp, remote_path)
                self._sftp_put_recursive(
                    sftp, lp, remote_path, on_line=on_line, cancelled=cancelled
                )
            finally:
                sftp.close()
                client.close()
            return
        ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
        src_arg = str(lp) + ("/" if lp.is_dir() else "")
        dest_arg = f"{self.username}@{self.ip}:{remote_path}"
        cmd = ["rsync", "-avz", "--update", "-e", ssh_cmd, src_arg, dest_arg]
        _log("[push] " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if on_proc:
            on_proc(proc)
        for line in proc.stdout:
            if cancelled and cancelled():
                proc.kill()
                proc.wait()
                raise RuntimeError("Cancelled.")
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        if proc.returncode not in (0, -9):  # -9 = SIGKILL from cancel
            raise RuntimeError(f"rsync push failed (exit {proc.returncode})")

    def pull_path(
        self,
        remote_path: str,
        local_path: str | Path,
        on_line=None,
        on_proc=None,
        cancelled=None,
    ):
        """Pull from absolute remote_path on the remote machine to local_path."""

        def _log(msg):
            print(msg)
            if on_line:
                on_line(msg)

        lp = Path(local_path).expanduser()
        _log(f"[pull] remote={remote_path}  local={lp}")
        has_rsync = (
            subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        )
        if self.ssh_password or not has_rsync:
            if not PARAMIKO_AVAILABLE:
                raise RuntimeError("paramiko is required. Run: pip install paramiko")
            client, sftp = self._sftp_client()
            try:
                remote_path = self._expand_remote_path(client, remote_path)
                _log(f"[pull] SFTP ← {remote_path}")
                try:
                    sftp.stat(remote_path)
                except FileNotFoundError:
                    raise FileNotFoundError(
                        f"Remote path does not exist: {remote_path}\n"
                        f"Check the Destination Path field or ensure the remote directory exists."
                    )
                self._sftp_get_recursive(
                    sftp, remote_path, lp, on_line=on_line, cancelled=cancelled
                )
            finally:
                sftp.close()
                client.close()
            return
        lp.mkdir(parents=True, exist_ok=True)
        ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
        src_arg = f"{self.username}@{self.ip}:{remote_path}/"
        cmd = ["rsync", "-avz", "--update", "-e", ssh_cmd, src_arg, str(lp)]
        _log("[pull] " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if on_proc:
            on_proc(proc)
        for line in proc.stdout:
            if cancelled and cancelled():
                proc.kill()
                proc.wait()
                raise RuntimeError("Cancelled.")
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        if proc.returncode not in (0, -9):  # -9 = SIGKILL from cancel
            raise RuntimeError(f"rsync pull failed (exit {proc.returncode})")


# ── Background thread for direct machine-to-machine sync ─────────────────────


class DirectSyncWorkerThread(QThread):
    """Runs a push_path / pull_path operation in a background thread."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(
        self,
        sync_obj: LocalNetworkSync,
        operation: str,
        local_path: str,
        remote_path: str,
    ):
        super().__init__()
        self.sync_obj = sync_obj
        self.operation = operation  # "push" or "pull"
        self.local_path = local_path
        self.remote_path = remote_path
        self._cancelled = False
        self._proc = None

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
        try:
            verb = "Pushing to" if self.operation == "push" else "Pulling from"
            self.progress.emit(f"── {verb} remote machine ──")
            if self.operation == "push":
                self.sync_obj.push_path(
                    self.local_path,
                    self.remote_path,
                    on_line=self.progress.emit,
                    on_proc=self._store_proc,
                    cancelled=lambda: self._cancelled,
                )
            else:
                self.sync_obj.pull_path(
                    self.remote_path,
                    self.local_path,
                    on_line=self.progress.emit,
                    on_proc=self._store_proc,
                    cancelled=lambda: self._cancelled,
                )
            if self._cancelled:
                self.finished.emit(False, "Cancelled.")
            else:
                self.finished.emit(
                    True,
                    "Push complete." if self.operation == "push" else "Pull complete.",
                )
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errmsg = "Cancelled." if self._cancelled else str(exc)
            self.progress.emit(f"✗ {errmsg}")
            self.finished.emit(False, errmsg)


class ConnectionTestThread(QThread):
    """Tests an SSH connection on a background thread and emits the result."""

    finished = pyqtSignal(bool, str)  # ok, message

    def __init__(self, sync_obj: LocalNetworkSync):
        super().__init__()
        self.sync_obj = sync_obj

    def run(self):
        try:
            ok, msg = self.sync_obj.test_connection()
        except Exception as exc:
            ok, msg = False, str(exc)
        self.finished.emit(ok, msg)


# ─────────────────────────────────────────────────────────────────────────────


class SyncApp(QMainWindow):

    # Signals emitted from rclone auth worker thread → main thread
    _rclone_auth_ok = pyqtSignal(str)  # provider
    _rclone_auth_err = pyqtSignal(str, str)  # provider, message
    _rclone_auth_token = pyqtSignal(str, str)  # provider, token_json

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Sync Tool")
        self.setGeometry(200, 200, 1000, 700)

        self.settings_file = self.get_settings_file_path()
        self.game_defaults = {}

        # Wire rclone auth signals (must be done before init_ui)
        self._rclone_auth_token.connect(self._apply_rclone_token)
        self._rclone_auth_ok.connect(self._on_rclone_authorized)
        self._rclone_auth_err.connect(self._on_rclone_auth_error)
        self.previous_paths = {}
        self.scanned_hosts = []  # list of (ip, os_type, label, mac, is_local)
        self._current_dest_mac = ""  # MAC of currently-selected destination
        self._current_dest_ip = ""  # IP  of currently-selected destination
        self.local_os = "Linux" if platform.system() != "Windows" else "Windows"
        self.local_interfaces, self.local_ips, self.local_macs = (
            self._get_local_network_identity()
        )

        self.scan_active = False
        self.sync_active = False
        self.scan_performed = False
        self._loading = False

        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.CustomizeWindowHint
        )
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        self.setup_darker_theme()
        self.init_ui()
        self.load_game_defaults()
        self.load_settings()
        self._apply_local_os_source_path()

        # ── Auto-save on any path/game/direction change ────────────────────────
        self.game_dropdown.currentIndexChanged.connect(
            self._on_game_or_direction_changed
        )
        self.sync_direction_dropdown.currentIndexChanged.connect(
            self._on_game_or_direction_changed
        )
        self.source_path.editingFinished.connect(self.save_settings)
        self.dest_path.editingFinished.connect(self.save_settings)
        self.cloud_folder_input.editingFinished.connect(self.save_settings)

        self.scan_active = False
        self.sync_active = False
        self.scan_performed = False
        self._loading = False
        self.scan_timer = QTimer(self)
        # Apply the saved interval (loaded before scan_timer was created)
        saved_interval_s = self.settings_scan_interval.value()
        self.scan_timer.setInterval(max(15, saved_interval_s) * 1000)
        self.scan_timer.timeout.connect(self.on_scan_timer_timeout)
        self.scan_timer.start()
        if self._should_auto_scan_network():  # respects saved autoscan checkbox
            self.start_network_scan()

        self._last_game_selected = self.game_dropdown.currentText()

        # ── Cloud state ───────────────────────────────────────────────────────
        self.rclone_gdrive: RcloneSync | None = None
        self.rclone_dropbox: RcloneSync | None = None
        self.local_network_sync: LocalNetworkSync | None = None
        self.lm_password: str = ""
        self.cloud_worker: CloudWorkerThread | None = None

    # ── Window helpers ────────────────────────────────────────────────────────

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def get_settings_file_path(self):
        game_sync_settings = "game_sync_settings.json"
        if platform.system() == "Windows":
            base = Path(os.getenv("APPDATA", "~"))
        else:
            base = Path.home()

        return base / game_sync_settings

    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _get_local_network_identity(self):
        interfaces = []
        local_ips = set()
        local_macs = set()

        if platform.system() != "Windows":
            try:
                result = subprocess.run(
                    ["ip", "-o", "-4", "addr", "show", "up", "scope", "global"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if "inet" not in parts:
                        continue

                    iface = parts[1]
                    ip = parts[parts.index("inet") + 1].split("/")[0]
                    mac_path = Path("/sys/class/net") / iface / "address"
                    mac = ""

                    if mac_path.exists():
                        mac = mac_path.read_text(encoding="utf-8").strip().lower()

                    interfaces.append({"iface": iface, "ip": ip, "mac": mac})
                    local_ips.add(ip)
                    if mac:
                        local_macs.add(mac)
            except Exception:
                pass

        fallback_ip = self._get_local_ip()
        if not interfaces and fallback_ip:
            interfaces.append({"iface": "local", "ip": fallback_ip, "mac": ""})
            local_ips.add(fallback_ip)

        return interfaces, local_ips, local_macs

    def _is_local_machine(self, ip, mac):
        normalized_mac = (mac or "").lower()
        return ip in self.local_ips or (
            normalized_mac and normalized_mac in self.local_macs
        )

    # ── Theme ─────────────────────────────────────────────────────────────────

    def setup_darker_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Highlight, QColor(87, 134, 193))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        self.setPalette(palette)
        self.setStyleSheet(
            "QWidget { background-color: #353535; color: white; }"
            "QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox, QGroupBox, QRadioButton, QCheckBox {"
            " background-color: #3f3f3f; color: white; border: 1px solid #555; }"
            "QPushButton { background-color: #444; color: white; border: 1px solid #555; }"
            "QPushButton:hover { background-color: #5a5a5a; }"
            "QLabel { color: white; }"
            "QScrollBar:vertical { background: #2b2b2b; width: 10px; }"
            "QScrollBar::handle:vertical { background: #626262; border-radius: 5px; }"
            "QScrollBar::handle:vertical:hover { background: #7a7a7a; }"
        )

    # ── UI ────────────────────────────────────────────────────────────────────

    def init_ui(self):
        outer_widget = QWidget()
        outer_layout = QVBoxLayout(outer_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        header_widget = QWidget()
        header_widget.setStyleSheet(
            "background-color: #2d2d2d; border-bottom: 1px solid #444;"
        )
        header_layout = QHBoxLayout(header_widget)

        # ── Local machine info ────────────────────────────────────────────────
        self.local_os_label = QLabel(f"Local machine OS: {self.local_os}")
        self.local_os_label.setStyleSheet("font-size: 11px; color: lightblue;")
        header_layout.addWidget(self.local_os_label)
        header_layout.setContentsMargins(10, 10, 10, 10)
        header_layout.setSpacing(8)
        header_layout.addStretch(1)

        header_label = QLabel("Game Sync Tool")
        header_label.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        header_layout.addWidget(header_label, alignment=Qt.AlignmentFlag.AlignCenter)
        header_layout.addStretch(1)

        window_control_layout = QHBoxLayout()
        window_control_layout.setAlignment(Qt.AlignmentFlag.AlignRight)

        minimize_button = QPushButton("_", self)
        minimize_button.setFixedSize(30, 20)
        minimize_button.clicked.connect(self.showMinimized)
        window_control_layout.addWidget(minimize_button)

        maximize_button = QPushButton("[ ]", self)
        maximize_button.setFixedSize(30, 20)
        maximize_button.clicked.connect(self.toggle_maximize)
        window_control_layout.addWidget(maximize_button)

        close_button = QPushButton("X", self)
        close_button.setFixedSize(30, 20)
        close_button.clicked.connect(self.close)
        window_control_layout.addWidget(close_button)

        header_layout.addLayout(window_control_layout)
        outer_layout.addWidget(header_widget)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 5, 10, 5)
        content_layout.setSpacing(5)

        description_label = QLabel(
            "Select your game, choose the destination machine, and start syncing your game files effortlessly."
        )
        description_label.setStyleSheet("font-size: 12px; color: gray;")
        content_layout.addWidget(
            description_label, alignment=Qt.AlignmentFlag.AlignCenter
        )

        # ── Game Selection ────────────────────────────────────────────────────
        self.select_game_label = QLabel("Select Game:")
        content_layout.addWidget(self.select_game_label)

        self.game_dropdown = QComboBox()
        content_layout.addWidget(self.game_dropdown)

        content_layout.addSpacing(10)
        # add line spacer between
        content_layout.addWidget(
            QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #555;"), 1
        )
        content_layout.addSpacing(10)

        # ── Cloud Storage accordion ───────────────────────────────────────────
        self.cloud_enabled_checkbox = QCheckBox(
            "Enable Cloud Storage (middle-man sync)"
        )
        self.cloud_enabled_checkbox.setStyleSheet(
            "font-size: 11px; color: #9fd3ff; font-weight: bold;"
        )
        self.cloud_enabled_checkbox.toggled.connect(self.toggle_cloud_section)
        content_layout.addWidget(self.cloud_enabled_checkbox)

        self.cloud_section = QGroupBox("Cloud Sync Settings")
        self.cloud_section.setVisible(False)
        self.cloud_section.setStyleSheet(
            "QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 6px; "
            "font-size: 11px; color: #9fd3ff; padding: 8px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )
        cloud_layout = QVBoxLayout()
        cloud_layout.setSpacing(6)

        # ── rclone availability banner ────────────────────────────────────────
        self.rclone_banner = QFrame()
        self.rclone_banner.setStyleSheet(
            "QFrame { background: #3b2a00; border: 1px solid #a06000; border-radius: 4px; padding: 4px; margin-bottom: 6px; }"
        )
        _banner_row = QHBoxLayout()
        _banner_row.setContentsMargins(1, 2, 1, 2)
        _banner_icon = QLabel("⚠")
        _banner_icon.setStyleSheet("color: #ffa500; font-size: 13px;")
        _banner_row.addWidget(_banner_icon)
        _banner_text = QLabel(
            "<b>rclone is not installed.</b>  "
            "Google Drive and Dropbox sync require rclone.  "
            "<a href='https://rclone.org/install/' style='color:#ffa500;'>Download rclone.org/install</a>"
        )
        _banner_text.setOpenExternalLinks(True)
        _banner_text.setStyleSheet(
            "font-size: 10px; color: #ffd080; background: transparent; border: none;"
        )
        _banner_text.setWordWrap(True)
        _banner_row.addWidget(_banner_text, 1)
        self.rclone_banner.setLayout(_banner_row)
        self.rclone_banner.setVisible(False)  # refreshed on show
        cloud_layout.addWidget(self.rclone_banner)

        # Provider row
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Provider:"))
        self.cloud_provider_group = QButtonGroup(self)
        for idx, name in enumerate(
            [
                "Google Drive",
                "Dropbox",
                "Both (GDrive+Dropbox)",
                "Local Network Machine",
            ]
        ):
            rb = QRadioButton(name)
            rb.setStyleSheet("font-size: 11px;")
            if idx == 0:
                rb.setChecked(True)
            self.cloud_provider_group.addButton(rb, idx)
            provider_row.addWidget(rb)
        provider_row.addStretch()
        cloud_layout.addLayout(provider_row)
        self.cloud_provider_group.idToggled.connect(self.on_cloud_provider_changed)

        # ── Google Drive sub-section ──────────────────────────────────────────
        self.gdrive_section = QWidget()
        gd_layout = QVBoxLayout()
        gd_layout.setContentsMargins(0, 0, 0, 0)
        gd_layout.setSpacing(4)

        gd_header = QLabel("— Google Drive —")
        gd_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        gd_layout.addWidget(gd_header)

        gd_note = QLabel(
            "Sign in with your Google account — no developer setup required."
        )
        gd_note.setStyleSheet("font-size: 10px; color: #aaa;")
        gd_layout.addWidget(gd_note)

        gd_btn_row = QHBoxLayout()
        self.gd_connect_btn = QPushButton("Authorize Google Drive")
        self.gd_connect_btn.setFixedWidth(180)
        self.gd_connect_btn.clicked.connect(lambda: self._authorize_rclone("gdrive"))
        gd_btn_row.addWidget(self.gd_connect_btn)
        self.gd_logout_btn = QPushButton("Log Out")
        self.gd_logout_btn.setFixedWidth(70)
        self.gd_logout_btn.setStyleSheet("color: #ff8080;")
        self.gd_logout_btn.clicked.connect(lambda: self._logout_rclone("gdrive"))
        self.gd_logout_btn.setVisible(False)
        gd_btn_row.addWidget(self.gd_logout_btn)
        self.gd_status_label = QLabel("Not authorized")
        self.gd_status_label.setStyleSheet("font-size: 10px; color: gray;")
        gd_btn_row.addWidget(self.gd_status_label)
        gd_btn_row.addStretch()
        gd_layout.addLayout(gd_btn_row)

        self.gdrive_section.setLayout(gd_layout)
        cloud_layout.addWidget(self.gdrive_section)

        # ── Dropbox sub-section ──────────────────────────────────────────────
        self.dropbox_section = QWidget()
        self.dropbox_section.setVisible(False)
        db_layout = QVBoxLayout()
        db_layout.setContentsMargins(0, 0, 0, 0)
        db_layout.setSpacing(4)

        db_header = QLabel("— Dropbox —")
        db_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        db_layout.addWidget(db_header)

        db_note = QLabel(
            "Sign in with your Dropbox account — no developer setup required."
        )
        db_note.setStyleSheet("font-size: 10px; color: #aaa;")
        db_layout.addWidget(db_note)

        db_btn_row = QHBoxLayout()
        self.db_connect_btn = QPushButton("Authorize Dropbox")
        self.db_connect_btn.setFixedWidth(180)
        self.db_connect_btn.clicked.connect(lambda: self._authorize_rclone("dropbox"))
        db_btn_row.addWidget(self.db_connect_btn)
        self.db_logout_btn = QPushButton("Log Out")
        self.db_logout_btn.setFixedWidth(70)
        self.db_logout_btn.setStyleSheet("color: #ff8080;")
        self.db_logout_btn.clicked.connect(lambda: self._logout_rclone("dropbox"))
        self.db_logout_btn.setVisible(False)
        db_btn_row.addWidget(self.db_logout_btn)
        self.db_status_label = QLabel("Not authorized")
        self.db_status_label.setStyleSheet("font-size: 10px; color: gray;")
        db_btn_row.addWidget(self.db_status_label)
        db_btn_row.addStretch()
        db_layout.addLayout(db_btn_row)

        self.dropbox_section.setLayout(db_layout)
        cloud_layout.addWidget(self.dropbox_section)

        # ── Local Network Machine sub-section ─────────────────────────────────
        self.local_machine_section = QWidget()
        self.local_machine_section.setVisible(False)
        lm_layout = QVBoxLayout()
        lm_layout.setContentsMargins(0, 0, 0, 0)
        lm_layout.setSpacing(4)

        lm_header = QLabel("— Local Network Machine —")
        lm_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        lm_layout.addWidget(lm_header)

        lm_host_row = QHBoxLayout()
        lm_host_label = QLabel("Machine:")
        lm_host_label.setFixedWidth(80)
        lm_host_row.addWidget(lm_host_label)
        self.lm_host_dropdown = QComboBox()
        self.lm_host_dropdown.addItem("— select from scanned machines —")
        self.lm_host_dropdown.currentIndexChanged.connect(self._on_lm_host_selected)
        lm_host_row.addWidget(self.lm_host_dropdown)
        lm_layout.addLayout(lm_host_row)

        self.lm_scan_progress = QProgressBar()
        self.lm_scan_progress.setRange(0, 0)
        self.lm_scan_progress.setVisible(False)
        self.lm_scan_progress.setFixedHeight(12)
        self.lm_scan_progress.setTextVisible(False)
        lm_layout.addWidget(self.lm_scan_progress)

        lm_user_row = QHBoxLayout()
        lm_user_label = QLabel("Username:")
        lm_user_label.setFixedWidth(80)
        lm_user_row.addWidget(lm_user_label)
        self.lm_username_input = QLineEdit()
        self.lm_username_input.setPlaceholderText("e.g.  pi  or  user")
        lm_user_row.addWidget(self.lm_username_input)
        lm_layout.addLayout(lm_user_row)

        lm_path_row = QHBoxLayout()
        lm_path_label = QLabel("Remote path:")
        lm_path_label.setFixedWidth(80)
        lm_path_row.addWidget(lm_path_label)
        self.lm_remote_path_input = QLineEdit()
        self.lm_remote_path_input.setPlaceholderText(
            "e.g.  `/home/pi/` or  `C:\\Users\\User\\`"
        )
        lm_path_row.addWidget(self.lm_remote_path_input)
        lm_layout.addLayout(lm_path_row)

        lm_key_row = QHBoxLayout()
        lm_key_label = QLabel("SSH key:")
        lm_key_label.setFixedWidth(80)
        lm_key_row.addWidget(lm_key_label)
        self.lm_ssh_key_input = QLineEdit()
        self.lm_ssh_key_input.setPlaceholderText(
            "(optional) path to private key, e.g. ~/.ssh/id_rsa"
        )
        lm_key_row.addWidget(self.lm_ssh_key_input)
        lm_browse_key_btn = QPushButton("Browse")
        lm_browse_key_btn.setFixedWidth(60)
        lm_browse_key_btn.clicked.connect(self._browse_ssh_key)
        lm_key_row.addWidget(lm_browse_key_btn)
        lm_layout.addLayout(lm_key_row)

        lm_port_test_row = QHBoxLayout()
        lm_port_label = QLabel("SSH port:")
        lm_port_label.setFixedWidth(80)
        lm_port_test_row.addWidget(lm_port_label)
        self.lm_port_input = QLineEdit("22")
        self.lm_port_input.setFixedWidth(50)
        lm_port_test_row.addWidget(self.lm_port_input)
        lm_port_test_row.addSpacing(10)
        self.lm_pass_btn = QPushButton("Set Password")
        self.lm_pass_btn.setFixedWidth(110)
        self.lm_pass_btn.clicked.connect(self._set_lm_password)
        lm_port_test_row.addWidget(self.lm_pass_btn)
        self.lm_test_btn = QPushButton("Test Connection")
        self.lm_test_btn.setFixedWidth(130)
        self.lm_test_btn.clicked.connect(self._test_local_machine_connection)
        lm_port_test_row.addWidget(self.lm_test_btn)
        self.lm_status_label = QLabel("Not configured")
        self.lm_status_label.setStyleSheet("font-size: 10px; color: gray;")
        lm_port_test_row.addWidget(self.lm_status_label)
        lm_port_test_row.addStretch()
        lm_layout.addLayout(lm_port_test_row)

        self.local_machine_section.setLayout(lm_layout)
        cloud_layout.addWidget(self.local_machine_section)

        # ── Cloud folder path ─────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #555;")
        cloud_layout.addWidget(sep)

        self.cloud_folder_row = QWidget()
        cloud_folder_layout = QHBoxLayout(self.cloud_folder_row)
        cloud_folder_layout.setContentsMargins(0, 0, 0, 0)
        cloud_folder_layout.addWidget(QLabel("Cloud Folder:"))
        self.cloud_folder_input = QLineEdit()
        self.cloud_folder_input.setPlaceholderText("/GameSync/<GameName>/")
        cloud_folder_layout.addWidget(self.cloud_folder_input)
        cloud_layout.addWidget(self.cloud_folder_row)

        self.cloud_section.setLayout(cloud_layout)
        content_layout.addWidget(self.cloud_section)

        content_layout.addSpacing(10)
        # add line spacer between
        content_layout.addWidget(
            QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #555;"), 1
        )
        content_layout.addSpacing(10)

        # ── Network Scan / Destination Machine ───────────────────────────────
        self.dest_machine_widget = QWidget()
        dest_machine_layout = QVBoxLayout(self.dest_machine_widget)
        dest_machine_layout.setContentsMargins(0, 0, 0, 0)
        dest_machine_layout.setSpacing(4)

        self.dest_machine_label = QLabel("Destination Machine (Network Scan):")
        dest_machine_layout.addWidget(self.dest_machine_label)

        scan_row = QHBoxLayout()
        self.scan_dropdown = QComboBox()
        self.scan_dropdown.addItem("— select a destination machine —")
        self.scan_dropdown.currentIndexChanged.connect(self.on_destination_selected)
        self.scan_dropdown.setEnabled(False)
        scan_row.addWidget(self.scan_dropdown)

        self.scan_button = QPushButton("Scan Network")
        self.scan_button.setFixedWidth(120)
        self.scan_button.clicked.connect(self.start_network_scan)
        scan_row.addWidget(self.scan_button)

        dest_machine_layout.addLayout(scan_row)

        self.scan_status_label = QLabel("")
        self.scan_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        dest_machine_layout.addWidget(self.scan_status_label)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setVisible(False)
        self.scan_progress.setFixedHeight(12)
        self.scan_progress.setTextVisible(False)
        dest_machine_layout.addWidget(self.scan_progress)

        content_layout.addWidget(self.dest_machine_widget)

        # ── Destination Machine SSH Credentials ───────────────────────────────
        self.dest_ssh_section = QWidget()
        self.dest_ssh_section.setVisible(False)
        dest_ssh_layout = QVBoxLayout(self.dest_ssh_section)
        dest_ssh_layout.setContentsMargins(30, 4, 30, 0)
        dest_ssh_layout.setSpacing(4)

        dest_ssh_header = QLabel("— Destination Machine SSH Credentials —")
        dest_ssh_header.setStyleSheet("font-size: 11px; color: #7ed6a9;")
        dest_ssh_layout.addWidget(dest_ssh_header)

        dest_ssh_user_row = QHBoxLayout()
        dest_ssh_user_label = QLabel("Username:")
        dest_ssh_user_label.setFixedWidth(80)
        dest_ssh_user_row.addWidget(dest_ssh_user_label)
        self.dest_ssh_user_input = QLineEdit()
        self.dest_ssh_user_input.setPlaceholderText("e.g. user or Administrator")
        dest_ssh_user_row.addWidget(self.dest_ssh_user_input)
        dest_ssh_layout.addLayout(dest_ssh_user_row)

        dest_ssh_key_row = QHBoxLayout()
        dest_ssh_key_label = QLabel("SSH Key:")
        dest_ssh_key_label.setFixedWidth(80)
        dest_ssh_key_row.addWidget(dest_ssh_key_label)
        self.dest_ssh_key_input = QLineEdit()
        self.dest_ssh_key_input.setPlaceholderText("(optional) path to private key")
        dest_ssh_key_row.addWidget(self.dest_ssh_key_input)
        dest_ssh_browse_btn = QPushButton("Browse")
        dest_ssh_browse_btn.setFixedWidth(60)
        dest_ssh_browse_btn.clicked.connect(self._browse_dest_ssh_key)
        dest_ssh_key_row.addWidget(dest_ssh_browse_btn)
        dest_ssh_layout.addLayout(dest_ssh_key_row)

        dest_ssh_port_row = QHBoxLayout()
        dest_ssh_port_label = QLabel("SSH Port:")
        dest_ssh_port_label.setFixedWidth(80)
        dest_ssh_port_row.addWidget(dest_ssh_port_label)
        self.dest_ssh_port_input = QLineEdit("22")
        self.dest_ssh_port_input.setFixedWidth(50)
        dest_ssh_port_row.addWidget(self.dest_ssh_port_input)
        dest_ssh_port_row.addSpacing(10)
        self.dest_ssh_pass_btn = QPushButton("Set Password")
        self.dest_ssh_pass_btn.setFixedWidth(110)
        self.dest_ssh_pass_btn.clicked.connect(self._set_dest_password)
        dest_ssh_port_row.addWidget(self.dest_ssh_pass_btn)
        self.dest_ssh_test_btn = QPushButton("Test Connection")
        self.dest_ssh_test_btn.setFixedWidth(130)
        self.dest_ssh_test_btn.clicked.connect(self._test_dest_connection)
        dest_ssh_port_row.addWidget(self.dest_ssh_test_btn)
        self.dest_ssh_status_label = QLabel("Not tested")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: gray;")
        dest_ssh_port_row.addWidget(self.dest_ssh_status_label)
        dest_ssh_port_row.addStretch()
        dest_ssh_layout.addLayout(dest_ssh_port_row)

        self.dest_ssh_progress = QProgressBar()
        self.dest_ssh_progress.setRange(0, 0)
        self.dest_ssh_progress.setVisible(False)
        self.dest_ssh_progress.setFixedHeight(12)
        self.dest_ssh_progress.setTextVisible(False)
        dest_ssh_layout.addWidget(self.dest_ssh_progress)

        content_layout.addWidget(self.dest_ssh_section)
        
        # ── Separator block shown only when cloud storage is disabled ─────────
        self.direct_only_top_spacer = QWidget()
        self.direct_only_top_spacer.setFixedHeight(10)
        content_layout.addWidget(self.direct_only_top_spacer)

        self.direct_only_separator = QFrame(frameShape=QFrame.Shape.HLine)
        self.direct_only_separator.setStyleSheet("color: #555;")
        content_layout.addWidget(self.direct_only_separator, 1)

        self.direct_only_bottom_spacer = QWidget()
        self.direct_only_bottom_spacer.setFixedHeight(10)
        content_layout.addWidget(self.direct_only_bottom_spacer)

        def _set_direct_only_sep_visible(enabled_cloud: bool):
            show = not enabled_cloud
            self.direct_only_top_spacer.setVisible(show)
            self.direct_only_separator.setVisible(show)
            self.direct_only_bottom_spacer.setVisible(show)

        _set_direct_only_sep_visible(self.cloud_enabled_checkbox.isChecked())
        self.cloud_enabled_checkbox.toggled.connect(_set_direct_only_sep_visible)
        # ── Source Path ───────────────────────────────────────────────────────
        self.source_label = QLabel("Source Path (this machine):")
        content_layout.addWidget(self.source_label)

        source_row = QHBoxLayout()
        self.source_path = QLineEdit()
        source_row.addWidget(self.source_path)
        self.source_default_btn = QPushButton("Default")
        self.source_default_btn.setFixedWidth(90)
        self.source_default_btn.clicked.connect(self._set_default_source_path)
        source_row.addWidget(self.source_default_btn)
        content_layout.addLayout(source_row)

        # ── Destination Path ──────────────────────────────────────────────────
        self.dest_label = QLabel("Destination Path (remote machine):")
        content_layout.addWidget(self.dest_label)

        dest_row = QHBoxLayout()
        self.dest_path = QLineEdit()
        dest_row.addWidget(self.dest_path)
        self.dest_default_btn = QPushButton("Default")
        self.dest_default_btn.setFixedWidth(90)
        self.dest_default_btn.clicked.connect(self._set_default_dest_path)
        dest_row.addWidget(self.dest_default_btn)
        content_layout.addLayout(dest_row)
        self.dest_label.setVisible(False)
        self.dest_path.setVisible(False)
        self.dest_default_btn.setVisible(False)

        # ── Sync Direction ────────────────────────────────────────────────────
        self.sync_direction_label = QLabel("Sync Direction:")
        content_layout.addWidget(self.sync_direction_label)

        self.sync_direction_dropdown = QComboBox()
        self.sync_direction_dropdown.addItems(
            [
                "Linux ↔ Linux",
                "Linux ↔ Windows",
                "Windows ↔ Linux",
                "Windows ↔ Windows",
            ]
        )
        content_layout.addWidget(self.sync_direction_dropdown)

        self.sync_direction_label.setVisible(False)
        self.sync_direction_dropdown.setVisible(False)

        content_layout.addSpacing(10)
        # add line spacer between
        content_layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, styleSheet="color: #555;"), 1)
        content_layout.addSpacing(10)

        # ── Sync Button ───────────────────────────────────────────────────────
        self.sync_button = QPushButton("⬆  Push to Dest")
        self.sync_button.setStyleSheet("background-color: #3a5a8a; color: white;")
        self.sync_button.setVisible(False)
        self.sync_button.clicked.connect(self.start_sync)

        self.pull_dest_btn = QPushButton("⬇  Pull from Dest")
        self.pull_dest_btn.setStyleSheet("background-color: #3a6a4a; color: white;")
        self.pull_dest_btn.setVisible(False)
        self.pull_dest_btn.clicked.connect(self.pull_from_dest)

        self.direct_sync_status_label = QLabel("")
        self.direct_sync_status_label.setStyleSheet(
            "font-size: 10px; color: lightgray;"
        )
        self.direct_sync_status_label.setVisible(False)

        self.push_cloud_btn = QPushButton("⬆  Push to Cloud")
        self.push_cloud_btn.setStyleSheet("background-color: #2a5f8a; color: white;")
        self.push_cloud_btn.setVisible(False)
        self.push_cloud_btn.clicked.connect(self.push_to_cloud)

        self.pull_cloud_btn = QPushButton("⬇  Pull from Cloud")
        self.pull_cloud_btn.setStyleSheet("background-color: #2a6b4a; color: white;")
        self.pull_cloud_btn.setVisible(False)
        self.pull_cloud_btn.clicked.connect(self.pull_from_cloud)

        self.cloud_op_status_label = QLabel("")
        self.cloud_op_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.cloud_op_status_label.setVisible(False)

        sync_btn_row = QHBoxLayout()
        sync_btn_row.addWidget(self.pull_dest_btn)
        sync_btn_row.addWidget(self.sync_button)
        sync_btn_row.addWidget(self.pull_cloud_btn)
        sync_btn_row.addWidget(self.push_cloud_btn)
        content_layout.addLayout(sync_btn_row)
        content_layout.addWidget(
            self.cloud_op_status_label, alignment=Qt.AlignmentFlag.AlignCenter
        )
        content_layout.addWidget(
            self.direct_sync_status_label, alignment=Qt.AlignmentFlag.AlignCenter
        )

        # ── Warning Label (large files warning) ────────────────────────────────
        self.warning_label = QLabel(
            "Syncing large game files may take time. Please be patient and do not interrupt the process."
        )
        self.warning_label.setStyleSheet("font-size: 10px; color: orange;")
        self.warning_label.setWordWrap(False)
        self.warning_label.setSizePolicy(
            QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        )
        
        content_layout.addWidget(self.warning_label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.warning_label.setVisible(False)  # only show when sync starts
        
        # ── Progress Bar ──────────────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        content_layout.addWidget(self.progress_bar)

        content_widget.setLayout(content_layout)

        # ── Scroll wrapper ────────────────────────────────────────────────────
        content_widget.setStyleSheet("background-color: #353535; color: white;")
        content_widget.setAutoFillBackground(True)

        scroll_area = QScrollArea()
        scroll_area.setWidget(content_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setStyleSheet(
            "QScrollArea { background-color: #353535; border: none; }"
            "QWidget { background-color: transparent; }"
        )

        # ── Footer tab widget ──────────────────────────────────────────────────
        footer_tabs = QTabWidget()
        footer_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #444; background: #2a2a2a; }"
            "QTabBar::tab { background: #353535; color: #aaa; padding: 4px 12px;"
            "  border: 1px solid #444; border-bottom: none; margin-right: 2px; }"
            "QTabBar::tab:selected { background: #2a2a2a; color: white; }"
            "QTabBar::tab:hover { background: #444; }"
        )

        # ── Tab 1: Sync Log ───────────────────────────────────────────────────
        log_tab = QWidget()
        log_tab.setStyleSheet("background-color: #2a2a2a;")
        log_vbox = QVBoxLayout(log_tab)
        log_vbox.setContentsMargins(4, 4, 4, 4)
        log_vbox.setSpacing(2)
        self.sync_log = QPlainTextEdit()
        self.sync_log.setReadOnly(True)
        self.sync_log.setMaximumBlockCount(1000)
        self.sync_log.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4;"
            " font-family: monospace; font-size: 11px; border: 1px solid #444; }"
        )
        self.sync_log.setMinimumHeight(120)
        log_vbox.addWidget(self.sync_log)
        footer_tabs.addTab(log_tab, "📋  Sync Log")

        # ── Tab 2: Settings ───────────────────────────────────────────────────
        settings_tab = QWidget()
        settings_tab.setStyleSheet("background-color: #2a2a2a;")
        settings_vbox = QVBoxLayout(settings_tab)
        settings_vbox.setContentsMargins(12, 8, 12, 8)
        settings_vbox.setSpacing(8)

        st_header = QLabel("Application Settings")
        st_header.setStyleSheet("font-size: 13px; font-weight: bold; color: #9fd3ff;")
        settings_vbox.addWidget(st_header)

        # Auto-scan on startup
        self.settings_autoscan_cb = QCheckBox("Auto-scan network on startup")
        self.settings_autoscan_cb.setChecked(True)
        self.settings_autoscan_cb.setToolTip(
            "Automatically scan the local network for machines when the app launches."
        )
        self.settings_autoscan_cb.toggled.connect(self.save_settings)
        settings_vbox.addWidget(self.settings_autoscan_cb)

        # Scan interval
        scan_interval_row = QHBoxLayout()
        scan_interval_lbl = QLabel("Background scan interval (seconds):")
        scan_interval_lbl.setFixedWidth(240)
        scan_interval_row.addWidget(scan_interval_lbl)
        self.settings_scan_interval = QSpinBox()
        self.settings_scan_interval.setRange(15, 3600)
        self.settings_scan_interval.setValue(60)
        self.settings_scan_interval.setFixedWidth(70)
        self.settings_scan_interval.setToolTip(
            "How often (in seconds) the background network re-scan fires."
        )
        self.settings_scan_interval.valueChanged.connect(self._on_scan_interval_changed)
        scan_interval_row.addWidget(self.settings_scan_interval)
        scan_interval_row.addStretch()
        settings_vbox.addLayout(scan_interval_row)

        # Confirm before sync
        self.settings_confirm_sync_cb = QCheckBox("Ask for confirmation before syncing")
        self.settings_confirm_sync_cb.setChecked(False)
        self.settings_confirm_sync_cb.setToolTip(
            "Show a confirmation dialog before each push or pull operation."
        )
        self.settings_confirm_sync_cb.toggled.connect(self.save_settings)
        settings_vbox.addWidget(self.settings_confirm_sync_cb)

        # Show sync log automatically
        self.settings_autoscroll_cb = QCheckBox("Auto-scroll sync log to latest entry")
        self.settings_autoscroll_cb.setChecked(True)
        self.settings_autoscroll_cb.setToolTip(
            "Keep the sync log scrolled to the most recent line."
        )
        self.settings_autoscroll_cb.toggled.connect(self.save_settings)
        settings_vbox.addWidget(self.settings_autoscroll_cb)

        settings_vbox.addStretch()
        footer_tabs.addTab(settings_tab, "⚙  Settings")
        
        # ── Tab 3: About ──────────────────────────────────────────────────────
        about_tab = QWidget()
        about_tab.setStyleSheet("background-color: #2a2a2a;")
        about_vbox = QVBoxLayout(about_tab)
        about_vbox.setContentsMargins(16, 10, 16, 10)
        about_vbox.setSpacing(6)

        about_title = QLabel("Game Sync Tool")
        about_title.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: white;"
        )
        about_vbox.addWidget(about_title)

        about_desc = QLabel(
            "Game Sync Tool lets you effortlessly transfer game save files between "
            "machines on your local network or via cloud storage (Google Drive / Dropbox). "
            "It supports Linux ↔ Windows cross-platform syncing and uses rclone for "
            "secure, reliable cloud transfers — no developer accounts required."
        )
        about_desc.setStyleSheet("font-size: 11px; color: #ccc;")
        about_desc.setWordWrap(True)
        about_vbox.addWidget(about_desc)

        about_how_title = QLabel("How to use")
        about_how_title.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #9fd3ff; margin-top: 4px;"
        )
        about_vbox.addWidget(about_how_title)

        how_to_text = QLabel(
            "1. Select your game from the dropdown.\n"
            "2. Choose <b>Cloud Storage</b> for cloud/cross-device sync, or leave it "
            "unchecked for direct LAN push/pull.\n"
            "3. Scan the network to discover nearby machines, then select a destination.\n"
            "4. Enter SSH credentials for the destination machine and test the connection.\n"
            "5. Hit <b>Push to Dest</b> to send saves, or <b>Pull from Dest</b> to receive them."
        )
        how_to_text.setStyleSheet("font-size: 10px; color: #bbb;")
        how_to_text.setWordWrap(True)
        about_vbox.addWidget(how_to_text)

        about_links_row = QHBoxLayout()

        github_btn = QPushButton("  View on GitHub")
        github_btn.setStyleSheet(
            "QPushButton { background-color: #24292e; color: white;"
            " border: 1px solid #555; padding: 4px 10px; font-size: 11px; }"
            "QPushButton:hover { background-color: #3a3f44; }"
        )
        github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        github_btn.clicked.connect(
            lambda: webbrowser.open("https://github.com/Perezented/Game-Sync")
        )
        about_links_row.addWidget(github_btn)

        donate_btn = QPushButton("  Donate via PayPal ♥")
        donate_btn.setStyleSheet(
            "QPushButton { background-color: #003087; color: white;"
            " border: 1px solid #0070ba; padding: 4px 10px; font-size: 11px; }"
            "QPushButton:hover { background-color: #0070ba; }"
        )
        donate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        donate_btn.clicked.connect(
            lambda: webbrowser.open(
                "https://www.paypal.com/ncp/payment/J4WYMPBFTLBMU"
            )
        )
        about_links_row.addWidget(donate_btn)
        about_links_row.addStretch()
        about_vbox.addLayout(about_links_row)

        about_vbox.addStretch()
        footer_tabs.addTab(about_tab, "ℹ  About")

        # ── Splitter: main scroll area + footer tabs ──────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle { background-color: #555; height: 4px; }"
        )
        splitter.addWidget(scroll_area)
        splitter.addWidget(footer_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([9999, 180])

        outer_layout.addWidget(splitter)
        self.setCentralWidget(outer_widget)



    # ── Cloud UI callbacks ────────────────────────────────────────────────────

    def toggle_cloud_section(self, enabled: bool):
        self.cloud_section.setVisible(enabled)
        if enabled:
            self._refresh_rclone_banner()
        # When cloud is active, hide direct-machine buttons; when not, hide cloud buttons
        cloud_on = enabled
        dest_selected = bool(self._current_dest_mac or self._current_dest_ip)
        self.sync_button.setVisible(not cloud_on)
        self.pull_dest_btn.setVisible(not cloud_on and dest_selected)
        self.push_cloud_btn.setVisible(cloud_on)
        self.pull_cloud_btn.setVisible(cloud_on)
        self.cloud_op_status_label.setVisible(cloud_on)
        self.direct_sync_status_label.setVisible(not cloud_on)
        self.sync_direction_label.setVisible(False)
        self.sync_direction_dropdown.setVisible(False)
        self.dest_machine_widget.setVisible(not cloud_on)
        self.dest_ssh_section.setVisible(not cloud_on and dest_selected)
        self.dest_label.setVisible(not cloud_on and dest_selected)
        self.dest_path.setVisible(not cloud_on and dest_selected)
        self.dest_default_btn.setVisible(not cloud_on and dest_selected)
        if cloud_on:
            self._refresh_cloud_folder_default()
        self.save_settings()

    def on_cloud_provider_changed(self, btn_id: int, checked: bool):
        if not checked:
            return
        # 0=GDrive, 1=Dropbox, 2=Both(GDrive+Dropbox), 3=LocalMachine
        self.gdrive_section.setVisible(btn_id in (0, 2))
        self.dropbox_section.setVisible(btn_id in (1, 2))
        self.local_machine_section.setVisible(btn_id == 3)
        # Cloud folder label changes context for local machine mode
        is_local = btn_id == 3
        self.cloud_folder_input.setPlaceholderText(
            "/GameSync/<GameName>/"
            if not is_local
            else "(sub-folder appended to remote path above, e.g. Zomboid)"
        )
        self.cloud_folder_row.setVisible(not is_local)
        if (
            btn_id == 3
            and self.lm_host_dropdown.count() <= 1
            and not getattr(self, "scan_active", False)
        ):
            self.start_network_scan()
        self._refresh_local_machine_scan_state()
        self._refresh_rclone_banner()

    def _refresh_cloud_folder_default(self):
        """Populate cloud folder with a game-specific saved path or default value."""
        game = self.game_dropdown.currentText() or "Game"
        saved_clouds = self.previous_paths.get("game_cloud_folders", {})
        saved_folder = saved_clouds.get(game)
        if saved_folder:
            self.cloud_folder_input.setText(saved_folder)
            return

        self.cloud_folder_input.setText(f"/GameSync/{game}/")

    def _refresh_local_machine_scan_state(self):
        has_hosts = self.lm_host_dropdown.count() > 1
        self.lm_host_dropdown.setEnabled(has_hosts)
        lm_active = (
            self.cloud_enabled_checkbox.isChecked()
            and self.cloud_provider_group.checkedId() == 3
        )
        self.lm_scan_progress.setVisible(self.scan_active and lm_active)

    def _remote_os_from_direction(self, direction: str) -> str:
        if "↔" not in direction:
            return "Linux"
        left, right = [part.strip() for part in direction.split("↔")]
        return right

    def _default_game_path(self, field: str) -> str:
        game = self.game_dropdown.currentText()
        if not game:
            return ""
        defaults = self.game_defaults.get(game, {})
        if field == "source":
            key = "linux" if self.local_os == "Linux" else "windows"
        else:
            remote_os = self._remote_os_from_direction(
                self.sync_direction_dropdown.currentText()
            )
            key = "linux" if remote_os == "Linux" else "windows"
        return defaults.get(key, "")

    def _set_default_source_path(self):
        default_path = self._default_game_path("source")
        if default_path:
            self.source_path.setText(default_path)
            self.save_settings()

    def _set_default_dest_path(self):
        default_path = self._default_game_path("dest")
        if default_path:
            self.dest_path.setText(default_path)
            self.save_settings()

    # ── Destination machine SSH helpers ───────────────────────────────────────

    def _browse_dest_ssh_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home() / ".ssh"), "All files (*)"
        )
        if path:
            self.dest_ssh_key_input.setText(path)

    def _set_lm_password(self):
        password, ok = QInputDialog.getText(
            self,
            "Local Machine SSH Password",
            "Enter SSH password for the local network machine:",
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            self.lm_password = password
            self.lm_pass_btn.setText("Password Set ✓")
            self.lm_pass_btn.setStyleSheet("color: #7ed6a9;")

    def _set_dest_password(self):
        password, ok = QInputDialog.getText(
            self,
            "Destination SSH Password",
            "Enter SSH password for the destination machine:",
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            self.dest_password = password
            self.dest_ssh_pass_btn.setText("Password Set ✓")
            self.dest_ssh_pass_btn.setStyleSheet("color: #7ed6a9;")

    def _build_dest_sync(self) -> "LocalNetworkSync | None":
        """Build a LocalNetworkSync for the currently selected destination machine."""
        ip = self._current_dest_ip
        usr = self.dest_ssh_user_input.text().strip()
        if not ip or not usr:
            return None
        port_txt = self.dest_ssh_port_input.text().strip()
        try:
            port = int(port_txt) if port_txt else 22
        except ValueError:
            port = 22
        key = self.dest_ssh_key_input.text().strip()
        password = getattr(self, "dest_password", "")
        return LocalNetworkSync(ip, usr, "/", port, key, password)

    def _test_dest_connection(self):
        usr = self.dest_ssh_user_input.text().strip()
        if not self._current_dest_ip or not usr:
            self.dest_ssh_status_label.setText(
                "Select a destination machine and enter username first."
            )
            self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return

        sync_obj = self._build_dest_sync()
        if sync_obj is None:
            return

        if not sync_obj.ssh_key and not sync_obj.ssh_password:
            password = self._set_dest_password()  # type: ignore[func-returns-value]
            # re-build with new password
            sync_obj = self._build_dest_sync()
            if sync_obj is None:
                return

        self.dest_ssh_test_btn.setEnabled(False)
        self.dest_ssh_test_btn.setStyleSheet("")
        self.dest_ssh_user_input.setStyleSheet("")
        self.dest_ssh_status_label.setText("Testing…")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.dest_ssh_progress.setVisible(True)

        print(
            f"[dest_test] spawning thread: ip={sync_obj.ip!r} port={sync_obj.ssh_port} user={sync_obj.username!r} key={sync_obj.ssh_key!r} has_pw={bool(sync_obj.ssh_password)}"
        )
        self._dest_test_thread = ConnectionTestThread(sync_obj)
        self._dest_test_thread.finished.connect(self._on_dest_test_done)
        self._dest_test_thread.start()

    def _on_dest_test_done(self, ok: bool, msg: str):
        self.dest_ssh_test_btn.setEnabled(True)
        self.dest_ssh_progress.setVisible(False)
        if ok:
            self.dest_ssh_status_label.setText("✓ " + msg[:70])
            self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self.dest_ssh_test_btn.setStyleSheet("border: 2px solid #7ed6a9;")
            self.dest_ssh_user_input.setStyleSheet("")
            self.save_settings()
        else:
            self.dest_ssh_status_label.setText("✗ " + msg[:70])
            self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: red;")
            self.dest_ssh_test_btn.setStyleSheet("border: 2px solid red;")
            self.dest_ssh_user_input.setStyleSheet("border: 1px solid red;")

    # ── Direct machine-to-machine sync ────────────────────────────────────────

    def _start_direct_sync(self, operation: str):
        """Common launcher for push/pull between this machine and the destination."""
        if getattr(self, "settings_confirm_sync_cb", None) and self.settings_confirm_sync_cb.isChecked():
            op_label = "Push to destination" if operation == "push" else "Pull from destination"
            reply = QMessageBox.question(
                self,
                "Confirm Sync",
                f"Are you sure you want to {op_label.lower()}?\nThis will overwrite files at the target.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        src = self.source_path.text().strip()
        dest = self.dest_path.text().strip()
        if not src or not dest:
            self.direct_sync_status_label.setText(
                "Source Path and Destination Path must both be set."
            )
            self.direct_sync_status_label.setStyleSheet(
                "font-size: 10px; color: orange;"
            )
            self.direct_sync_status_label.setVisible(True)
            return

        sync_obj = self._build_dest_sync()
        if sync_obj is None:
            self.direct_sync_status_label.setText(
                "Fill in Destination SSH Username (and credentials) first."
            )
            self.direct_sync_status_label.setStyleSheet(
                "font-size: 10px; color: orange;"
            )
            self.direct_sync_status_label.setVisible(True)
            return

        if not sync_obj.ssh_key and not sync_obj.ssh_password:
            self._set_dest_password()
            sync_obj = self._build_dest_sync()
            if not getattr(self, "dest_password", ""):
                return  # user cancelled password entry

        local_path = src if operation == "push" else dest
        remote_path = dest if operation == "push" else src

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.direct_sync_status_label.setVisible(True)
        self.warning_label.setVisible(True)
        self.save_settings()

        self._direct_worker = DirectSyncWorkerThread(
            sync_obj, operation, local_path, remote_path
        )
        self._direct_worker.progress.connect(self._on_direct_sync_progress)
        self._direct_worker.finished.connect(self._on_direct_sync_finished)
        self._direct_worker.start()

        # Turn the active button into a Cancel button; disable the other
        if operation == "push":
            self.sync_button.setEnabled(True)
            self.sync_button.setText("⏹  Cancel Push")
            self.sync_button.setStyleSheet("background-color: #8a3a3a; color: white;")
            try:
                self.sync_button.clicked.disconnect()
            except Exception:
                pass
            self.sync_button.clicked.connect(self._cancel_direct_sync)
            self.pull_dest_btn.setEnabled(False)
        else:
            self.sync_button.setVisible(True)
            self.pull_dest_btn.setVisible(True)
            self.pull_dest_btn.setEnabled(True)
            self.pull_dest_btn.setText("⏹  Cancel Pull")
            self.pull_dest_btn.setStyleSheet("background-color: #8a3a3a; color: white;")
            try:
                self.pull_dest_btn.clicked.disconnect()
            except Exception:
                pass
            self.pull_dest_btn.clicked.connect(self._cancel_direct_sync)
            self.sync_button.setEnabled(False)

    def _log_append(self, msg: str):
        """Append a line to the sync log panel."""
        self.sync_log.appendPlainText(msg)
        # auto-scroll to bottom (if setting enabled)
        if not getattr(self, "settings_autoscroll_cb", None) or self.settings_autoscroll_cb.isChecked():
            sb = self.sync_log.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_direct_sync_progress(self, msg: str):
        self.direct_sync_status_label.setText(msg[:120])
        self._log_append(msg)

    def _on_direct_sync_finished(self, ok: bool, msg: str):
        # Restore both buttons to their default state
        try:
            self.sync_button.clicked.disconnect()
        except Exception:
            pass
        self.sync_button.clicked.connect(self.start_sync)
        self.sync_button.setText("⬆  Push to Dest")
        self.sync_button.setStyleSheet("background-color: #3a5a8a; color: white;")
        self.sync_button.setEnabled(True)

        try:
            self.pull_dest_btn.clicked.disconnect()
        except Exception:
            pass
        self.pull_dest_btn.clicked.connect(self.pull_from_dest)
        self.pull_dest_btn.setText("⬇  Pull from Dest")
        self.pull_dest_btn.setStyleSheet("background-color: #3a6a4a; color: white;")
        self.pull_dest_btn.setEnabled(True)

        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if ok else 0)
        QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False))
        color = "#7ed6a9" if ok else ("orange" if msg == "Cancelled." else "red")
        self.direct_sync_status_label.setText(("✓ " if ok else "✗ ") + msg)
        self.direct_sync_status_label.setStyleSheet(f"font-size: 10px; color: {color};")
        self._log_append(("✓ " if ok else "✗ ") + msg)

    def _cancel_direct_sync(self):
        worker = getattr(self, "_direct_worker", None)
        if worker and worker.isRunning():
            self._log_append("── Cancelling…")
            worker.cancel()

    def pull_from_dest(self):
        self._start_direct_sync("pull")

    # ── Google Drive auth ─────────────────────────────────────────────────────

    def _authorize_rclone(self, provider: str):
        """Run 'rclone authorize' for the given provider. Opens browser, captures token."""
        import threading  # noqa: PLC0415
        import re as _re  # noqa: PLC0415

        status_label = (
            self.gd_status_label if provider == "gdrive" else self.db_status_label
        )
        connect_btn = (
            self.gd_connect_btn if provider == "gdrive" else self.db_connect_btn
        )
        rclone_type = "drive" if provider == "gdrive" else "dropbox"

        if not shutil.which("rclone"):
            status_label.setText("rclone not found — install from rclone.org")
            status_label.setStyleSheet("font-size: 10px; color: red;")
            return

        connect_btn.setEnabled(False)
        status_label.setText("Opening browser… waiting for authorization…")
        status_label.setStyleSheet("font-size: 10px; color: lightgray;")

        # Capture loop variables for thread closure
        _provider = provider
        _rclone_type = rclone_type

        def _run():
            try:
                result = subprocess.run(
                    [
                        "rclone",
                        "authorize",
                        _rclone_type,
                        "--auth-no-open-browser=false",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                output = result.stdout + result.stderr
                # Robust extraction: split on the "--->"/"<---End paste" markers
                if "---" + ">" in output and "<---End paste" in output:
                    token_json = (
                        output.split("---" + ">", 1)[1]
                        .split("<---End paste", 1)[0]
                        .strip()
                    )
                else:
                    # Fallback: try regex for slightly different rclone output formats
                    m = _re.search(
                        r"--->(\s*\{.*?\}\s*)<---End paste", output, _re.DOTALL
                    )
                    if m:
                        token_json = m.group(1).strip()
                    else:
                        raise RuntimeError(
                            f"Could not parse rclone token.\nOutput was:\n{output[-300:]}"
                        )
                self._rclone_auth_token.emit(_provider, token_json)
            except Exception as exc:
                self._rclone_auth_err.emit(_provider, str(exc)[:200])

        threading.Thread(target=_run, daemon=True).start()

    def _apply_rclone_token(self, provider: str, token_json: str):
        """Called on main thread via signal after successful rclone authorize."""
        if provider == "gdrive":
            self.rclone_gdrive = RcloneSync("gdrive", token_json)
            self.previous_paths["rclone_gdrive_token"] = token_json
        else:
            self.rclone_dropbox = RcloneSync("dropbox", token_json)
            self.previous_paths["rclone_dropbox_token"] = token_json
        self.save_settings()
        self._rclone_auth_ok.emit(provider)

    def _on_rclone_authorized(self, provider: str):
        status_label = (
            self.gd_status_label if provider == "gdrive" else self.db_status_label
        )
        connect_btn = (
            self.gd_connect_btn if provider == "gdrive" else self.db_connect_btn
        )
        logout_btn = self.gd_logout_btn if provider == "gdrive" else self.db_logout_btn
        connect_btn.setEnabled(True)
        connect_btn.setText("Re-authorize")
        logout_btn.setVisible(True)
        status_label.setText("✓ Authorized")
        status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")

    def _logout_rclone(self, provider: str):
        """Clear stored token and reset auth state for the given provider."""
        if provider == "gdrive":
            self.rclone_gdrive = None
            self.previous_paths.pop("rclone_gdrive_token", None)
            status_label = self.gd_status_label
            connect_btn = self.gd_connect_btn
            logout_btn = self.gd_logout_btn
        else:
            self.rclone_dropbox = None
            self.previous_paths.pop("rclone_dropbox_token", None)
            status_label = self.db_status_label
            connect_btn = self.db_connect_btn
            logout_btn = self.db_logout_btn
        # Delete the rclone config file for this provider
        cfg_path = (
            Path.home() / ".config" / "game-sync-tool" / f"rclone_{provider}.conf"
        )
        try:
            cfg_path.unlink(missing_ok=True)
        except Exception:
            pass
        self.save_settings()
        logout_btn.setVisible(False)
        connect_btn.setText(
            "Authorize Google Drive" if provider == "gdrive" else "Authorize Dropbox"
        )
        status_label.setText("Not authorized")
        status_label.setStyleSheet("font-size: 10px; color: gray;")

    def _refresh_rclone_banner(self):
        """Show the rclone-not-found banner only when relevant providers are selected."""
        rclone_missing = not shutil.which("rclone")
        btn_id = (
            self.cloud_provider_group.checkedId()
        )  # 0=GDrive,1=Dropbox,2=Both,3=Local
        needs_rclone = btn_id in (0, 1, 2)  # not Local Network
        self.rclone_banner.setVisible(rclone_missing and needs_rclone)

    def _on_rclone_auth_error(self, provider: str, msg: str):
        status_label = (
            self.gd_status_label if provider == "gdrive" else self.db_status_label
        )
        connect_btn = (
            self.gd_connect_btn if provider == "gdrive" else self.db_connect_btn
        )
        connect_btn.setEnabled(True)
        status_label.setText(f"Error: {msg}")
        status_label.setStyleSheet("font-size: 10px; color: red;")

    # ── Cloud push / pull ─────────────────────────────────────────────────────

    def _active_cloud_sync_objects(self) -> list:
        """Return whichever cloud sync objects are ready based on selected provider."""
        btn_id = (
            self.cloud_provider_group.checkedId()
        )  # 0=GDrive, 1=Dropbox, 2=Both, 3=Local
        objects = []
        if (
            btn_id in (0, 2)
            and self.rclone_gdrive
            and self.rclone_gdrive.is_authenticated()
        ):
            objects.append(("Google Drive", self.rclone_gdrive))
        if (
            btn_id in (1, 2)
            and self.rclone_dropbox
            and self.rclone_dropbox.is_authenticated()
        ):
            objects.append(("Dropbox", self.rclone_dropbox))
        if (
            btn_id == 3
            and self.local_network_sync
            and self.local_network_sync.is_authenticated()
        ):
            objects.append(("Local Machine", self.local_network_sync))
        return objects

    def _cloud_folder_for_game(self) -> str:
        game = self.game_dropdown.currentText() or "Game"
        # Local Machine mode: cloud_folder_row is hidden, always use just the
        # game name so it gets appended cleanly to remote_base (e.g.
        # /home/user/GameSync + ProjectZomboid → /home/user/GameSync/ProjectZomboid).
        if self.cloud_provider_group.checkedId() == 3:
            return game
        folder = self.cloud_folder_input.text().strip()
        if not folder:
            folder = f"/GameSync/{game}/"
        return folder

    def push_to_cloud(self):
        if getattr(self, "settings_confirm_sync_cb", None) and self.settings_confirm_sync_cb.isChecked():
            reply = QMessageBox.question(
                self, "Confirm Push",
                "Are you sure you want to push saves to the cloud?\nThis will overwrite files in the cloud folder.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        local_path = self.source_path.text().strip()
        if not local_path:
            self.cloud_op_status_label.setText("Set a Source Path first.")
            return
        cloud_syncs = self._active_cloud_sync_objects()
        if not cloud_syncs:
            self.cloud_op_status_label.setText(
                "No authenticated cloud provider available."
            )
            return
        self._run_cloud_op("upload", cloud_syncs, local_path)

    def pull_from_cloud(self):
        if getattr(self, "settings_confirm_sync_cb", None) and self.settings_confirm_sync_cb.isChecked():
            reply = QMessageBox.question(
                self, "Confirm Pull",
                "Are you sure you want to pull saves from the cloud?\nThis will overwrite local files.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        local_path = self.dest_path.text().strip() or self.source_path.text().strip()
        if not local_path:
            self.cloud_op_status_label.setText("Set a Destination Path first.")
            return
        cloud_syncs = self._active_cloud_sync_objects()
        if not cloud_syncs:
            self.cloud_op_status_label.setText(
                "No authenticated cloud provider available."
            )
            return
        self._run_cloud_op("download", cloud_syncs, local_path)

    def _run_cloud_op(self, operation: str, cloud_syncs: list, local_path: str):
        # For simplicity run sequentially using the first available provider.
        # Multi-provider: run each in sequence via the same thread chain.
        cloud_folder = self._cloud_folder_for_game()
        name, sync_obj = cloud_syncs[0]

        self.push_cloud_btn.setEnabled(False)
        self.pull_cloud_btn.setEnabled(False)
        self.cloud_op_status_label.setText(f"{operation.title()}ing via {name}…")
        self.progress_bar.setVisible(True)
        self.warning_label.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.cloud_worker = CloudWorkerThread(
            operation, sync_obj, local_path, cloud_folder
        )
        self.cloud_worker.progress.connect(self.cloud_op_status_label.setText)
        self.cloud_worker.progress.connect(self._log_append)
        self.cloud_worker.finished.connect(
            lambda ok, msg: self._on_cloud_op_finished(
                ok, msg, cloud_syncs[1:], operation, local_path, cloud_folder
            )
        )
        self.cloud_worker.start()

        # Turn the active button into a Cancel button
        if operation == "upload":
            self.push_cloud_btn.setEnabled(True)
            self.push_cloud_btn.setText("⏹  Cancel Push")
            self.push_cloud_btn.setStyleSheet(
                "background-color: #8a3a3a; color: white;"
            )
            try:
                self.push_cloud_btn.clicked.disconnect()
            except Exception:
                pass
            self.push_cloud_btn.clicked.connect(self._cancel_cloud_sync)
            self.pull_cloud_btn.setEnabled(False)
        else:
            self.pull_cloud_btn.setEnabled(True)
            self.pull_cloud_btn.setText("⏹  Cancel Pull")
            self.pull_cloud_btn.setStyleSheet(
                "background-color: #8a3a3a; color: white;"
            )
            try:
                self.pull_cloud_btn.clicked.disconnect()
            except Exception:
                pass
            self.pull_cloud_btn.clicked.connect(self._cancel_cloud_sync)
            self.push_cloud_btn.setEnabled(False)

    def _on_cloud_op_finished(
        self,
        ok: bool,
        msg: str,
        remaining: list,
        operation: str,
        local_path: str,
        cloud_folder: str,
    ):
        cancelled = getattr(self, "_cloud_cancelled", False)
        if not ok or cancelled:
            label = (
                "Cancelled."
                if cancelled or msg == "Cancelled."
                else f"Error: {msg[:80]}"
            )
            color = "orange" if cancelled or msg == "Cancelled." else "red"
            self._log_append(
                ("✗ " if not ok else "⏹ ") + (msg if not cancelled else "Cancelled.")
            )
            self.cloud_op_status_label.setText(label)
            self.cloud_op_status_label.setStyleSheet(
                f"font-size: 10px; color: {color};"
            )
            self._reset_cloud_buttons()
            return

        if remaining:
            # Chain to next provider
            name, sync_obj = remaining[0]
            self.cloud_op_status_label.setText(f"{operation.title()}ing via {name}…")
            self.cloud_worker = CloudWorkerThread(
                operation, sync_obj, local_path, cloud_folder
            )
            self.cloud_worker.progress.connect(self.cloud_op_status_label.setText)
            self.cloud_worker.progress.connect(self._log_append)
            self.cloud_worker.finished.connect(
                lambda ok2, msg2: self._on_cloud_op_finished(
                    ok2, msg2, remaining[1:], operation, local_path, cloud_folder
                )
            )
            self.cloud_worker.start()
        else:
            self._log_append(f"✓ {msg}")
            self.cloud_op_status_label.setText(f"✓ {msg}")
            self.cloud_op_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self._reset_cloud_buttons()

    def _cancel_cloud_sync(self):
        self._cloud_cancelled = True
        worker = self.cloud_worker
        if worker and worker.isRunning():
            self._log_append("── Cancelling cloud sync…")
            worker.cancel()

    def _reset_cloud_buttons(self):
        self._cloud_cancelled = False
        try:
            self.push_cloud_btn.clicked.disconnect()
        except Exception:
            pass
        self.push_cloud_btn.clicked.connect(self.push_to_cloud)
        self.push_cloud_btn.setText("⬆  Push to Cloud")
        self.push_cloud_btn.setStyleSheet("background-color: #2a5f8a; color: white;")
        self.push_cloud_btn.setEnabled(True)
        try:
            self.pull_cloud_btn.clicked.disconnect()
        except Exception:
            pass
        self.pull_cloud_btn.clicked.connect(self.pull_from_cloud)
        self.pull_cloud_btn.setText("⬇  Pull from Cloud")
        self.pull_cloud_btn.setStyleSheet("background-color: #2a6b4a; color: white;")
        self.pull_cloud_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.warning_label.setVisible(False)

    # ── Local network machine helpers ─────────────────────────────────────────

    def populate_local_cloud_dropdown(self):
        """Re-fill the local machine dropdown from whatever was last scanned."""
        current_idx = self.lm_host_dropdown.currentIndex()
        current_text = self.lm_host_dropdown.currentText()

        self.lm_host_dropdown.blockSignals(True)
        self.lm_host_dropdown.clear()
        self.lm_host_dropdown.addItem("— select from scanned machines —")

        for ip, os_type, label, mac, is_local in self.scanned_hosts:
            if not is_local:
                self.lm_host_dropdown.addItem(label)

        # Re-select previously selected machine if still present
        saved_ip = self.previous_paths.get("lm_ip", "")
        if saved_ip:
            for i in range(self.lm_host_dropdown.count()):
                if saved_ip in self.lm_host_dropdown.itemText(i):
                    self.lm_host_dropdown.setCurrentIndex(i)
                    break
        elif current_text and current_text != "— select from scanned machines —":
            idx = self.lm_host_dropdown.findText(current_text)
            if idx >= 0:
                self.lm_host_dropdown.setCurrentIndex(idx)

        self.lm_host_dropdown.blockSignals(False)
        self._refresh_local_machine_scan_state()

    def _on_lm_host_selected(self, index: int):
        if index <= 0:
            return
        # Find the IP from the label and store it; try to auto-fill username hints
        label = self.lm_host_dropdown.currentText()
        for ip, os_type, disp_label, mac, is_local in self.scanned_hosts:
            if disp_label == label:
                self.previous_paths["lm_ip"] = ip
                # Auto-suggest username based on detected OS
                if not self.lm_username_input.text():
                    self.lm_username_input.setPlaceholderText(
                        "pi  (Raspberry Pi?)" if os_type == "Linux" else "Administrator"
                    )
                break
        self._build_local_network_sync()

    def _browse_ssh_key(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SSH Private Key", str(Path.home() / ".ssh"), "All files (*)"
        )
        if path:
            self.lm_ssh_key_input.setText(path)

    def _request_local_machine_password(self) -> str | None:
        password, ok = QInputDialog.getText(
            self,
            "SSH Password",
            "Enter the SSH password for the selected local machine:",
            QLineEdit.EchoMode.Password,
        )
        if ok and password:
            self.lm_password = password
            return password
        return None

    def _build_local_network_sync(self) -> bool:
        """Create a LocalNetworkSync from the current UI fields. Returns True if valid."""
        ip = self.previous_paths.get("lm_ip", "")
        username = self.lm_username_input.text().strip()
        rpath = self.lm_remote_path_input.text().strip()
        port_txt = self.lm_port_input.text().strip()
        key = self.lm_ssh_key_input.text().strip()

        # Pull IP from dropdown if not yet stored
        if not ip:
            label = self.lm_host_dropdown.currentText()
            for ip2, _, disp, _, _ in self.scanned_hosts:
                if disp == label:
                    ip = ip2
                    break

        try:
            port = int(port_txt) if port_txt else 22
        except ValueError:
            port = 22

        if not ip or not username or not rpath:
            return False

        password = getattr(self, "lm_password", "")
        self.local_network_sync = LocalNetworkSync(
            ip, username, rpath, port, key, password
        )
        return True

    def _test_local_machine_connection(self):
        if not self._build_local_network_sync():
            self.lm_status_label.setText(
                "Fill in Machine, Username, and Remote Path first."
            )
            self.lm_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return

        if (
            not self.local_network_sync.ssh_key
            and not self.local_network_sync.ssh_password
        ):
            password = self._request_local_machine_password()
            if not password:
                self.lm_status_label.setText(
                    "SSH password required or provide an SSH key."
                )
                self.lm_status_label.setStyleSheet("font-size: 10px; color: orange;")
                return
            self.local_network_sync.ssh_password = password

        self.lm_test_btn.setEnabled(False)
        self.lm_test_btn.setStyleSheet("")
        self.lm_status_label.setText("Testing…")
        self.lm_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.lm_scan_progress.setVisible(True)

        obj = self.local_network_sync
        print(
            f"[lm_test] spawning thread: ip={obj.ip!r} port={obj.ssh_port} user={obj.username!r} key={obj.ssh_key!r} has_pw={bool(obj.ssh_password)}"
        )
        self._lm_test_thread = ConnectionTestThread(obj)
        self._lm_test_thread.finished.connect(self._on_lm_test_done)
        self._lm_test_thread.start()

    def _on_lm_test_done(self, ok: bool, msg: str):
        self.lm_test_btn.setEnabled(True)
        self.lm_scan_progress.setVisible(False)
        if ok:
            self.lm_status_label.setText("✓ " + msg[:70])
            self.lm_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self.lm_test_btn.setStyleSheet("border: 2px solid #7ed6a9;")
            self.save_settings()
        else:
            self.lm_status_label.setText("✗ " + msg[:70])
            self.lm_status_label.setStyleSheet("font-size: 10px; color: red;")
            self.lm_test_btn.setStyleSheet("border: 2px solid red;")

    # ── Network scan ──────────────────────────────────────────────────────────

    def on_scan_timer_timeout(self):
        if self.sync_active or not self._should_auto_scan_network():
            return
        self.start_network_scan()

    def _should_auto_scan_network(self) -> bool:
        if not getattr(self, "settings_autoscan_cb", None) or not self.settings_autoscan_cb.isChecked():
            return False
        if self._current_dest_mac:
            return False
        if self.scan_dropdown.count() <= 1 and not self.scan_performed:
            return True
        return False

    def _on_scan_interval_changed(self, value: int):
        if hasattr(self, "scan_timer"):
            self.scan_timer.setInterval(value * 1000)
        self.save_settings()

    def _update_scan_button_label(self):
        if self.scan_dropdown.currentIndex() > 0:
            self.scan_button.setText("Rescan Network")
        else:
            self.scan_button.setText("Scan Network")

    def start_network_scan(self):
        if self.scan_active:
            return

        self.scan_performed = True
        self.scan_active = True
        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning...")
        self._refresh_local_machine_scan_state()
        self.scan_dropdown.clear()
        self.scan_dropdown.addItem("Scanning…")
        self.scan_dropdown.setEnabled(False)
        self.scan_status_label.setText("Scanning LAN for hosts…")
        self.scan_progress.setVisible(True)
        self.scanned_hosts = []

        self.scanner = NetworkScanner()
        self.scanner.scan_status.connect(self.scan_status_label.setText)
        self.scanner.scan_complete.connect(self.on_scan_complete)
        self.scanner.start()

    def on_scan_complete(self, hosts):
        self.scan_active = False
        prepared_hosts = []
        seen_hosts = set()
        self.scan_dropdown.clear()
        self.scan_dropdown.addItem("— select a destination machine —")

        for ip, os_type, label, mac in hosts:
            normalized_mac = (mac or "").lower()
            host_key = (ip, normalized_mac)
            if host_key in seen_hosts:
                continue

            is_local = self._is_local_machine(ip, normalized_mac)
            display_label = f"{label} (this machine)" if is_local else label
            prepared_hosts.append(
                (ip, os_type, display_label, normalized_mac, is_local)
            )
            seen_hosts.add(host_key)

        for interface in self.local_interfaces:
            ip = interface["ip"]
            mac = (interface["mac"] or "").lower()
            host_key = (ip, mac)
            if host_key in seen_hosts:
                continue

            hostname = socket.gethostname()
            iface_name = interface["iface"]
            display_label = (
                f"{ip}  ({hostname} / {iface_name})  [{self.local_os}] (this machine)"
            )
            prepared_hosts.append((ip, self.local_os, display_label, mac, True))
            seen_hosts.add(host_key)

        self.scanned_hosts = prepared_hosts

        last_dest_mac = self.previous_paths.get("last_dest_mac", "").lower()
        last_dest_ip = self.previous_paths.get("last_dest_ip", "")
        auto_select_index = 0

        # ── Group entries by hostname (covers multi-NIC, WiFi+Ethernet+Tailscale) ──
        import re  # noqa: PLC0415
        from collections import defaultdict  # noqa: PLC0415
        from PyQt6.QtGui import QFont  # noqa: PLC0415

        def _extract_hostname(label: str) -> str:
            """Pull the hostname out of labels like 'IP  (hostname)  [OS]'.
            Returns the IP itself as a fallback if no parenthetical is found."""
            m = re.search(r"\(([^)]+)\)", label)
            if m:
                # For local-machine labels the parens contain 'hostname / iface'
                return m.group(1).split("/")[0].strip().lower()
            return label.split()[0]  # fall back to bare IP

        # hostname → list of scanned_hosts indices
        hostname_to_indices: dict[str, list[int]] = defaultdict(list)
        for i, (_ip, _os, _lbl, _mac, _local) in enumerate(prepared_hosts):
            key = _extract_hostname(_lbl)
            hostname_to_indices[key].append(i)

        def _add_host_item(host_idx: int, indent: bool = False) -> None:
            nonlocal auto_select_index
            h_ip, h_os, h_label, h_mac, h_local = prepared_hosts[host_idx]
            display = ("    " + h_label) if indent else h_label
            self.scan_dropdown.addItem(display)
            di = self.scan_dropdown.count() - 1
            # Store the scanned_hosts index in UserRole so on_destination_selected
            # can retrieve it regardless of how many group-header rows were inserted.
            self.scan_dropdown.setItemData(di, host_idx)
            if h_local:
                self.scan_dropdown.setItemData(
                    di, QColor("orange"), Qt.ItemDataRole.ForegroundRole
                )
            elif auto_select_index == 0:
                if (last_dest_mac and h_mac and h_mac == last_dest_mac) or (
                    last_dest_ip and h_ip == last_dest_ip
                ):
                    auto_select_index = di

        for hostname_key, indices in hostname_to_indices.items():
            if len(indices) > 1:
                # Header: show the human-readable hostname + interface count
                # Use the resolved hostname from the first entry's label parenthetical,
                # falling back to the raw hostname_key.
                first_label = prepared_hosts[indices[0]][2]
                m = re.search(r"\(([^)]+)\)", first_label)
                display_name = m.group(1).split("/")[0].strip() if m else hostname_key
                self.scan_dropdown.addItem(
                    f"▸  {display_name}  [{len(indices)} interfaces]"
                )
                hdr_di = self.scan_dropdown.count() - 1
                self.scan_dropdown.setItemData(hdr_di, -1)  # not a selectable host
                hdr_item = self.scan_dropdown.model().item(hdr_di)
                hdr_item.setFlags(Qt.ItemFlag.NoItemFlags)  # not selectable/focusable
                hdr_item.setForeground(QColor("#aaaaaa"))
                hdr_font = QFont(hdr_item.font())
                hdr_font.setItalic(True)
                hdr_item.setFont(hdr_font)
                for host_idx in indices:
                    _add_host_item(host_idx, indent=True)
            else:
                _add_host_item(indices[0], indent=False)

        self.scan_button.setEnabled(True)
        self._update_scan_button_label()
        self.scan_progress.setVisible(False)
        self.scan_dropdown.setEnabled(self.scan_dropdown.count() > 1)

        # Keep the local-cloud machine dropdown in sync with scan results
        self.populate_local_cloud_dropdown()
        self._refresh_local_machine_scan_state()

        # Auto-select the last-used destination machine if it was found
        if auto_select_index > 0:
            self.scan_dropdown.setCurrentIndex(auto_select_index)
            self.scan_status_label.setText(
                self.scan_status_label.text() + "  (last destination auto-selected)"
            )

    def on_destination_selected(self, index):
        """Auto-set sync direction and paths when a scanned machine is selected."""
        if index <= 0:
            self._current_dest_mac = ""
            self._current_dest_ip = ""
            self.dest_ssh_section.setVisible(False)
            self.pull_dest_btn.setVisible(False)
            self.sync_button.setVisible(False)
            self.direct_sync_status_label.setVisible(False)
            self.dest_label.setVisible(False)
            self.dest_path.setVisible(False)
            self.dest_default_btn.setVisible(False)
            self._update_scan_button_label()
            return

        # item data holds the scanned_hosts index; -1 means a non-selectable group header
        host_idx = self.scan_dropdown.itemData(index)
        if (
            host_idx is None
            or not isinstance(host_idx, int)
            or host_idx < 0
            or host_idx >= len(self.scanned_hosts)
        ):
            # Group header row clicked — ignore (item is non-selectable so this
            # normally won't fire, but guard anyway)
            self.scan_dropdown.setCurrentIndex(0)
            return

        dest_ip, remote_os, _label, dest_mac, is_local = self.scanned_hosts[host_idx]
        if is_local:
            self.scan_status_label.setText(
                "This entry is the current machine. Choose another destination."
            )
            self._update_scan_button_label()
            return

        self._current_dest_mac = dest_mac
        self._current_dest_ip = dest_ip

        # Show destination SSH credentials section (only when cloud is not active)
        cloud_on = self.cloud_enabled_checkbox.isChecked()
        self.dest_ssh_section.setVisible(not cloud_on)
        self.pull_dest_btn.setVisible(not cloud_on)
        self.sync_button.setVisible(not cloud_on)
        self.direct_sync_status_label.setVisible(not cloud_on)
        self.dest_label.setVisible(not cloud_on)
        self.dest_path.setVisible(not cloud_on)
        self.dest_default_btn.setVisible(not cloud_on)

        # Load saved credentials for this destination machine
        saved_creds = self.previous_paths.get("dest_machine_creds", {}).get(
            dest_mac, {}
        )
        if saved_creds.get("username"):
            self.dest_ssh_user_input.setText(saved_creds["username"])
        elif not self.dest_ssh_user_input.text():
            # Suggest username based on detected OS
            self.dest_ssh_user_input.setPlaceholderText(
                "username / user" if remote_os == "Linux" else "Administrator"
            )
        if saved_creds.get("ssh_key"):
            self.dest_ssh_key_input.setText(saved_creds["ssh_key"])
        if saved_creds.get("port"):
            self.dest_ssh_port_input.setText(str(saved_creds["port"]))
        # Reset password button appearance (password is never persisted)
        self.dest_password = ""
        self.dest_ssh_pass_btn.setText("Set Password")
        self.dest_ssh_pass_btn.setStyleSheet("")
        self.dest_ssh_test_btn.setStyleSheet("")
        self.dest_ssh_user_input.setStyleSheet("")
        self.dest_ssh_status_label.setText("Not tested")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: gray;")

        # Update in-memory record of last destination before setting direction
        # and paths so that _game_machine_key() returns the correct key when
        # update_paths() / save_settings() are called below.
        self.previous_paths["last_dest_mac"] = dest_mac
        self.previous_paths["last_dest_ip"] = dest_ip

        self._set_sync_direction(self.local_os, remote_os)
        self.update_paths()
        # Save AFTER paths have been recalculated for the correct destination OS,
        # so we never persist stale (wrong-OS) paths under this machine's key.
        self.save_settings()
        self._update_scan_button_label()

    # ── OS / path helpers ─────────────────────────────────────────────────────

    def _apply_local_os_source_path(self):
        """Pre-fill source path from game defaults based on the local OS."""
        game_name = self.game_dropdown.currentText()
        if not game_name or game_name not in self.game_defaults:
            return
        defaults = self.game_defaults[game_name]
        key = "linux" if self.local_os == "Linux" else "windows"
        if not self.previous_paths.get("source_path"):
            self.source_path.setText(defaults.get(key, ""))

    def _set_sync_direction(self, local_os, remote_os):
        """Pick the matching sync direction item from the dropdown."""
        label = f"{local_os} ↔ {remote_os}"
        idx = self.sync_direction_dropdown.findText(label)
        if idx >= 0:
            self.sync_direction_dropdown.blockSignals(True)
            self.sync_direction_dropdown.setCurrentIndex(idx)
            self.sync_direction_dropdown.blockSignals(False)

    # ── Data / settings ───────────────────────────────────────────────────────

    def load_game_defaults(self):
        try:
            defaults_path = Path(__file__).parent / "game_defaults.json"
            with open(defaults_path, "r") as f:
                data = json.load(f)
                for game in data["games"]:
                    self.game_defaults[game["name"]] = game["defaults"]
            self.game_dropdown.addItems(self.game_defaults.keys())
        except Exception as err:
            print(f"Error loading game defaults: {err}")

    def load_settings(self):
        if not self.settings_file.exists():
            return
        self._loading = True
        try:
            with open(self.settings_file, "r") as f:
                self.previous_paths = json.load(f)
                game = self.previous_paths.get("game")
                if game:
                    self.game_dropdown.setCurrentText(game)
                sync_direction = self.previous_paths.get("sync_direction")
                if sync_direction:
                    self.sync_direction_dropdown.setCurrentText(sync_direction)

                # Restore saved paths for this game with no destination selected yet
                # (key = "{game}__")
                key = f"{game or ''}__"
                saved = self.previous_paths.get("game_machine_paths", {}).get(key, {})
                self.source_path.setText(
                    saved.get("source_path", self.previous_paths.get("source_path", ""))
                )
                self.dest_path.setText(
                    saved.get("dest_path", self.previous_paths.get("dest_path", ""))
                )

                # ── Cloud settings ────────────────────────────────────────────
                cloud_enabled = self.previous_paths.get("cloud_enabled", False)
                self.cloud_enabled_checkbox.setChecked(cloud_enabled)

                provider_idx = self.previous_paths.get("cloud_provider_idx", 0)
                btn = self.cloud_provider_group.button(provider_idx)
                if btn:
                    btn.setChecked(True)

                self.cloud_folder_input.setText(
                    self.previous_paths.get("cloud_folder", "")
                )
                self._refresh_cloud_folder_default()
                self._last_game_selected = self.game_dropdown.currentText()

                # Google Drive (via rclone)
                if self.previous_paths.get("rclone_gdrive_token"):
                    self.rclone_gdrive = RcloneSync(
                        "gdrive", self.previous_paths["rclone_gdrive_token"]
                    )
                    self.gd_status_label.setText("✓ Authorized")
                    self.gd_status_label.setStyleSheet(
                        "font-size: 10px; color: #7ed6a9;"
                    )
                    self.gd_logout_btn.setVisible(True)
                    self.gd_connect_btn.setText("Re-authorize")

                # Dropbox (via rclone)
                if self.previous_paths.get("rclone_dropbox_token"):
                    self.rclone_dropbox = RcloneSync(
                        "dropbox", self.previous_paths["rclone_dropbox_token"]
                    )
                    self.db_status_label.setText("✓ Authorized")
                    self.db_status_label.setStyleSheet(
                        "font-size: 10px; color: #7ed6a9;"
                    )
                    self.db_logout_btn.setVisible(True)
                    self.db_connect_btn.setText("Re-authorize")

                # ── Application settings tab ────────────────────────────────────
                self.settings_autoscan_cb.setChecked(
                    self.previous_paths.get("settings_autoscan", True)
                )
                self.settings_scan_interval.setValue(
                    int(self.previous_paths.get("settings_scan_interval", 60))
                )
                self.settings_confirm_sync_cb.setChecked(
                    self.previous_paths.get("settings_confirm_sync", False)
                )
                self.settings_autoscroll_cb.setChecked(
                    self.previous_paths.get("settings_autoscroll", True)
                )

                # Local network machine
                self.lm_username_input.setText(
                    self.previous_paths.get("lm_username", "")
                )
                self.lm_remote_path_input.setText(
                    self.previous_paths.get("lm_remote_path", "")
                )
                self.lm_port_input.setText(self.previous_paths.get("lm_port", "22"))
                self.lm_ssh_key_input.setText(self.previous_paths.get("lm_ssh_key", ""))
                if self.previous_paths.get("lm_ip") and self.previous_paths.get(
                    "lm_username"
                ):
                    self.lm_status_label.setText(
                        f"Saved: {self.previous_paths['lm_ip']} "
                        f"({self.previous_paths['lm_username']})"
                    )
                    self.lm_status_label.setStyleSheet(
                        "font-size: 10px; color: lightgray;"
                    )

        except Exception as err:
            print(f"Could not load settings: {err}")
        finally:
            self._loading = False

        # If dest_path is still empty after restoring settings, fill it from
        # the game defaults so the field is never left blank on first launch
        # or when no path has been saved yet for the current game.
        if not self.dest_path.text():
            self.update_paths()

    def _game_machine_key(self) -> str:
        """Unique key for the current (game, destination-MAC) combination."""
        game = self.game_dropdown.currentText() or "__unknown__"
        return f"{game}__{self._current_dest_mac}"

    def _on_game_or_direction_changed(self):
        """Called when the game or sync-direction dropdown changes."""
        if getattr(self, "_loading", False):
            return
        current_game = self.game_dropdown.currentText()
        if current_game != getattr(self, "_last_game_selected", ""):
            self._refresh_cloud_folder_default()
            self._last_game_selected = current_game
        self.update_paths()
        self.save_settings()

    def update_paths(self):
        selected_game = self.game_dropdown.currentText()
        selected_direction = self.sync_direction_dropdown.currentText()

        if not selected_game:
            return

        # 1. Start with game defaults
        defaults = self.game_defaults.get(selected_game, {})
        if selected_direction == "Linux ↔ Linux":
            src = defaults.get("linux", "")
            dst = defaults.get("linux", "")
        elif selected_direction == "Linux ↔ Windows":
            src = defaults.get("linux", "")
            dst = defaults.get("windows", "")
        elif selected_direction == "Windows ↔ Linux":
            src = defaults.get("windows", "")
            dst = defaults.get("linux", "")
        elif selected_direction == "Windows ↔ Windows":
            src = defaults.get("windows", "")
            dst = defaults.get("windows", "")
        else:
            src = dst = ""

        # 2. Override with any saved paths for this specific game + destination machine
        #    Use 'or' so an empty saved string still falls back to the game default.
        key = self._game_machine_key()
        saved = self.previous_paths.get("game_machine_paths", {}).get(key, {})
        src = saved.get("source_path") or src
        dst = saved.get("dest_path") or dst

        self.source_path.setText(src)
        self.dest_path.setText(dst)

    def save_settings(self):
        if getattr(self, "_loading", False):
            return

        # Preserve all previously loaded keys (cloud tokens, etc.) then overlay
        settings = dict(self.previous_paths)

        settings["game"] = self.game_dropdown.currentText()
        settings["sync_direction"] = self.sync_direction_dropdown.currentText()

        # ── Per-game + destination-machine path persistence ───────────────────
        key = self._game_machine_key()
        game_machine_paths = settings.get("game_machine_paths", {})
        game_machine_paths[key] = {
            "source_path": self.source_path.text(),
            "dest_path": self.dest_path.text(),
            "sync_direction": self.sync_direction_dropdown.currentText(),
        }
        settings["game_machine_paths"] = game_machine_paths

        # ── Game-specific cloud folder persistence ─────────────────────────────
        game_cloud_folders = settings.get("game_cloud_folders", {})
        game_cloud_folders[self.game_dropdown.currentText() or "__unknown__"] = (
            self.cloud_folder_input.text()
        )
        settings["game_cloud_folders"] = game_cloud_folders

        # ── Destination machine SSH credentials (username + key, no password) ──
        if self._current_dest_mac:
            dest_machine_creds = settings.get("dest_machine_creds", {})
            dest_machine_creds[self._current_dest_mac] = {
                "username": self.dest_ssh_user_input.text(),
                "ssh_key": self.dest_ssh_key_input.text(),
                "port": self.dest_ssh_port_input.text(),
            }
            settings["dest_machine_creds"] = dest_machine_creds

        # ── Last destination machine ──────────────────────────────────────────
        if self._current_dest_mac:
            settings["last_dest_mac"] = self._current_dest_mac
            settings["last_dest_ip"] = self._current_dest_ip

        # ── Application settings tab ──────────────────────────────────────────
        settings["settings_autoscan"] = self.settings_autoscan_cb.isChecked()
        settings["settings_scan_interval"] = self.settings_scan_interval.value()
        settings["settings_confirm_sync"] = self.settings_confirm_sync_cb.isChecked()
        settings["settings_autoscroll"] = self.settings_autoscroll_cb.isChecked()

        # ── Cloud UI state ────────────────────────────────────────────────────
        settings["cloud_enabled"] = self.cloud_enabled_checkbox.isChecked()
        settings["cloud_provider_idx"] = self.cloud_provider_group.checkedId()
        settings["cloud_folder"] = self.cloud_folder_input.text()
        settings["rclone_gdrive_token"] = self.previous_paths.get(
            "rclone_gdrive_token", ""
        )
        settings["rclone_dropbox_token"] = self.previous_paths.get(
            "rclone_dropbox_token", ""
        )
        # Local machine
        settings["lm_ip"] = self.previous_paths.get("lm_ip", "")
        settings["lm_username"] = self.lm_username_input.text()
        settings["lm_remote_path"] = self.lm_remote_path_input.text()
        settings["lm_port"] = self.lm_port_input.text()
        settings["lm_ssh_key"] = self.lm_ssh_key_input.text()

        try:
            with open(self.settings_file, "w") as f:
                json.dump(settings, f, indent=2)
            self.previous_paths = settings
        except Exception as err:
            print(f"Could not save settings: {err}")

    def start_sync(self):
        self._start_direct_sync("push")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SyncApp()
    window.show()
    sys.exit(app.exec())
