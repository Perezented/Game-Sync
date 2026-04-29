import subprocess
import shutil
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

try:
    import paramiko

    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


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
        return shutil.which("sshpass") is not None

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
        rsync = shutil.which("rsync") is not None
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
        rsync = shutil.which("rsync") is not None
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

        lp = (
            Path(
                str(local_path)
                .replace("%USERPROFILE%", str(Path.home()))
                .replace("%APPDATA%", str(Path.home() / "AppData" / "Roaming"))
            )
            .expanduser()
            .resolve()
        )
        _log(f"[push] local={lp}  remote={remote_path}")
        if not lp.exists():
            raise FileNotFoundError(
                f"Local source path does not exist: {lp}\n"
                f"Check the Source Path field in the UI."
            )
        has_rsync = shutil.which("rsync") is not None
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

        lp = (
            Path(
                local_path.replace("%USERPROFILE%", str(Path.home())).replace(
                    "%APPDATA%", str(Path.home() / "AppData" / "Roaming")
                )
            )
            .expanduser()
            .resolve()
        )
        _log(f"[pull] remote={remote_path}  local={lp}")
        has_rsync = shutil.which("rsync") is not None
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
