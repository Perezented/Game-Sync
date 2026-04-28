import sys
import os
import socket
import ipaddress
import concurrent.futures
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QPushButton,
    QFileDialog, QLabel, QLineEdit, QComboBox, QProgressBar, QStyle, QSizePolicy,
    QCheckBox, QGroupBox, QRadioButton, QButtonGroup, QFrame, QScrollArea,
    QInputDialog
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
import shlex
import json
import platform
import webbrowser
import urllib.parse

# ── Optional cloud dependencies (installed separately) ────────────────────────
try:
    from googleapiclient.discovery import build as gdrive_build
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials as GCredentials
    from google.auth.transport.requests import Request as GRequest
    GDRIVE_AVAILABLE = True
except ImportError:
    GDRIVE_AVAILABLE = False

try:
    import dropbox as _dropbox_module
    from dropbox import DropboxOAuth2FlowNoRedirect
    DROPBOX_AVAILABLE = True
except ImportError:
    DROPBOX_AVAILABLE = False

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


class NetworkScanner(QThread):
    """Scans the local /24 subnet for live hosts and guesses their OS."""
    scan_complete = pyqtSignal(list)  # list of (ip, os_type, label, mac)
    scan_status   = pyqtSignal(str)   # progress messages

    # Port -> OS hint, checked in priority order
    OS_PORTS = [
        (445,  "Windows"),  # SMB
        (3389, "Windows"),  # RDP
        (22,   "Linux"),    # SSH
    ]

    def run(self):
        self.scan_status.emit("Scanning network…")
        local_ip = self._get_local_ip()
        if not local_ip:
            self.scan_status.emit("Could not determine local IP.")
            self.scan_complete.emit([])
            return

        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        hosts   = [str(h) for h in network.hosts()]

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
        alive   = False

        for port, hint in self.OS_PORTS:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.4)
                if s.connect_ex((ip, port)) == 0:
                    alive   = True
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

class GoogleDriveSync:
    """Thin wrapper around the Google Drive v3 REST API."""

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

    def __init__(self, client_id: str, client_secret: str, token_data: dict | None = None):
        self.client_id     = client_id
        self.client_secret = client_secret
        self.creds         = None
        if token_data and GDRIVE_AVAILABLE:
            try:
                self.creds = GCredentials.from_authorized_user_info(token_data, self.SCOPES)
            except Exception:
                self.creds = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self) -> dict:
        """Run the local-server OAuth flow. Returns serialisable token dict."""
        if not GDRIVE_AVAILABLE:
            raise RuntimeError(
                "google-api-python-client is not installed.\n"
                "Run:  pip install google-api-python-client google-auth-oauthlib"
            )
        client_config = {
            "installed": {
                "client_id":      self.client_id,
                "client_secret":  self.client_secret,
                "auth_uri":       "https://accounts.google.com/o/oauth2/auth",
                "token_uri":      "https://oauth2.googleapis.com/token",
                "redirect_uris":  ["http://localhost"],
            }
        }
        flow       = InstalledAppFlow.from_client_config(client_config, self.SCOPES)
        self.creds = flow.run_local_server(port=0)
        return json.loads(self.creds.to_json())

    def is_authenticated(self) -> bool:
        if not self.creds or not GDRIVE_AVAILABLE:
            return False
        if self.creds.expired and self.creds.refresh_token:
            try:
                self.creds.refresh(GRequest())
            except Exception:
                return False
        return self.creds.valid

    def refreshed_token(self) -> dict | None:
        """Return the (potentially refreshed) token as a dict, or None."""
        if self.creds and GDRIVE_AVAILABLE:
            return json.loads(self.creds.to_json())
        return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _service(self):
        return gdrive_build("drive", "v3", credentials=self.creds)

    def _get_or_create_folder(self, svc, folder_path: str, parent_id: str = "root") -> str:
        parts = [p for p in folder_path.strip("/").split("/") if p]
        for part in parts:
            q       = (f"name='{part}' and "
                       f"mimeType='application/vnd.google-apps.folder' and "
                       f"'{parent_id}' in parents and trashed=false")
            results = svc.files().list(q=q, fields="files(id)").execute()
            files   = results.get("files", [])
            if files:
                parent_id = files[0]["id"]
            else:
                meta      = {"name": part,
                             "mimeType": "application/vnd.google-apps.folder",
                             "parents": [parent_id]}
                parent_id = svc.files().create(body=meta, fields="id").execute()["id"]
        return parent_id

    def _upload_file(self, svc, file_path: Path, folder_id: str):
        from googleapiclient.http import MediaFileUpload  # noqa: PLC0415
        q       = f"name='{file_path.name}' and '{folder_id}' in parents and trashed=false"
        results = svc.files().list(q=q, fields="files(id)").execute()
        files   = results.get("files", [])
        media   = MediaFileUpload(str(file_path), resumable=True)
        if files:
            svc.files().update(fileId=files[0]["id"], media_body=media).execute()
        else:
            meta = {"name": file_path.name, "parents": [folder_id]}
            svc.files().create(body=meta, media_body=media).execute()

    def _download_folder(self, svc, folder_id: str, local_dir: Path):
        import io  # noqa: PLC0415
        from googleapiclient.http import MediaIoBaseDownload  # noqa: PLC0415
        results = svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType)"
        ).execute()
        for item in results.get("files", []):
            if item["mimeType"] == "application/vnd.google-apps.folder":
                sub = local_dir / item["name"]
                sub.mkdir(exist_ok=True)
                self._download_folder(svc, item["id"], sub)
            else:
                request = svc.files().get_media(fileId=item["id"])
                buf     = io.BytesIO()
                dl      = MediaIoBaseDownload(buf, request)
                done    = False
                while not done:
                    _, done = dl.next_chunk()
                (local_dir / item["name"]).write_bytes(buf.getvalue())

    # ── Public upload / download ──────────────────────────────────────────────

    def upload(self, local_path: str | Path, cloud_folder: str):
        svc       = self._service()
        folder_id = self._get_or_create_folder(svc, cloud_folder)
        lp        = Path(local_path)
        if lp.is_file():
            self._upload_file(svc, lp, folder_id)
        elif lp.is_dir():
            for f in lp.rglob("*"):
                if f.is_file():
                    rel       = f.relative_to(lp.parent)
                    sub_path  = cloud_folder.rstrip("/") + "/" + "/".join(rel.parts[:-1])
                    sub_id    = self._get_or_create_folder(svc, sub_path) if rel.parts[:-1] else folder_id
                    self._upload_file(svc, f, sub_id)

    def download(self, cloud_folder: str, local_path: str | Path):
        svc       = self._service()
        folder_id = self._get_or_create_folder(svc, cloud_folder)
        lp        = Path(local_path)
        lp.mkdir(parents=True, exist_ok=True)
        self._download_folder(svc, folder_id, lp)


# ── Dropbox helper ────────────────────────────────────────────────────────────

class DropboxSync:
    """OAuth2 Dropbox client (offline refresh token so it survives app restarts)."""

    def __init__(self, app_key: str, app_secret: str,
                 access_token: str = "", refresh_token: str = ""):
        self.app_key       = app_key
        self.app_secret    = app_secret
        self.access_token  = access_token
        self.refresh_token = refresh_token
        self._auth_flow    = None
        self._dbx          = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    def get_auth_url(self) -> str:
        """Start PKCE flow and return the authorisation URL the user should open."""
        if not DROPBOX_AVAILABLE:
            raise RuntimeError(
                "dropbox package is not installed.\n"
                "Run:  pip install dropbox"
            )
        self._auth_flow = DropboxOAuth2FlowNoRedirect(
            self.app_key,
            consumer_secret    = self.app_secret,
            token_access_type  = "offline",
        )
        return self._auth_flow.start()

    def finish_auth(self, code: str) -> dict:
        """Exchange the user-pasted code for tokens. Returns token dict."""
        if not self._auth_flow:
            raise RuntimeError("Call get_auth_url() first.")
        result             = self._auth_flow.finish(code.strip())
        self.access_token  = result.access_token
        self.refresh_token = getattr(result, "refresh_token", "")
        self._dbx          = None  # force re-init
        return {"access_token": self.access_token, "refresh_token": self.refresh_token}

    def is_authenticated(self) -> bool:
        return bool(self.access_token)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_dbx(self):
        if not self._dbx:
            kwargs = dict(app_key=self.app_key, app_secret=self.app_secret)
            if self.refresh_token:
                kwargs["oauth2_access_token"]  = self.access_token
                kwargs["oauth2_refresh_token"] = self.refresh_token
            else:
                kwargs["oauth2_access_token"] = self.access_token
            self._dbx = _dropbox_module.Dropbox(**kwargs)
        return self._dbx

    def _upload_file(self, dbx, file_path: Path, cloud_path: str):
        with open(file_path, "rb") as fh:
            data = fh.read()
        dbx.files_upload(
            data, cloud_path,
            mode=_dropbox_module.files.WriteMode.overwrite
        )

    def _download_folder_entries(self, dbx, result, local_dir: Path):
        for entry in result.entries:
            if isinstance(entry, _dropbox_module.files.FolderMetadata):
                sub = local_dir / entry.name
                sub.mkdir(exist_ok=True)
                sub_result = dbx.files_list_folder(entry.path_lower)
                self._download_folder_entries(dbx, sub_result, sub)
            elif isinstance(entry, _dropbox_module.files.FileMetadata):
                _, resp = dbx.files_download(entry.path_lower)
                (local_dir / entry.name).write_bytes(resp.content)
        if result.has_more:
            more = dbx.files_list_folder_continue(result.cursor)
            self._download_folder_entries(dbx, more, local_dir)

    # ── Public upload / download ──────────────────────────────────────────────

    def upload(self, local_path: str | Path, cloud_folder: str):
        dbx          = self._get_dbx()
        cloud_folder = cloud_folder.rstrip("/")
        lp           = Path(local_path)
        if lp.is_file():
            self._upload_file(dbx, lp, f"{cloud_folder}/{lp.name}")
        elif lp.is_dir():
            for f in lp.rglob("*"):
                if f.is_file():
                    rel  = f.relative_to(lp.parent)
                    dest = f"{cloud_folder}/{'/'.join(rel.parts)}"
                    self._upload_file(dbx, f, dest)

    def download(self, cloud_folder: str, local_path: str | Path):
        dbx    = self._get_dbx()
        lp     = Path(local_path)
        lp.mkdir(parents=True, exist_ok=True)
        result = dbx.files_list_folder(cloud_folder.rstrip("/"))
        self._download_folder_entries(dbx, result, lp)


# ── Background thread for cloud operations ────────────────────────────────────

class CloudWorkerThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, operation: str, sync_obj, local_path: str, cloud_folder: str):
        super().__init__()
        self.operation   = operation  # "upload" or "download"
        self.sync_obj    = sync_obj
        self.local_path  = local_path
        self.cloud_folder = cloud_folder

    def run(self):
        try:
            self.progress.emit(f"{self.operation.title()}ing to/from cloud…")
            if self.operation == "upload":
                self.sync_obj.upload(self.local_path, self.cloud_folder)
                self.finished.emit(True, "Upload complete.")
            else:
                self.sync_obj.download(self.cloud_folder, self.local_path)
                self.finished.emit(True, "Download complete.")
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ── Local network machine ("private cloud") helper ────────────────────────────

class LocalNetworkSync:
    """Push/pull saves to/from any SSH-accessible machine on the LAN (e.g. a Pi)."""

    def __init__(self, ip: str, username: str, remote_base: str,
                 ssh_port: int = 22, ssh_key: str = "", ssh_password: str = ""):
        self.ip           = ip
        self.username     = username
        self.remote_base  = remote_base.rstrip("/")
        self.ssh_port     = ssh_port
        self.ssh_key      = ssh_key  # path to private key, optional
        self.ssh_password = ssh_password

    def is_authenticated(self) -> bool:
        return bool(self.ip and self.username)

    def _ssh_opts(self, batch_mode: bool = False) -> list[str]:
        opts = [
            "-p", str(self.ssh_port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=5",
            "-o", "LogLevel=ERROR",
            "-o", "PreferredAuthentications=publickey,password",
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
            cmd = self._with_password(["ssh"] + self._ssh_opts(batch_mode=batch_mode) + [
                f"{self.username}@{self.ip}", "echo OK"
            ])
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
        """Recursively create remote directories via SFTP."""
        parts = remote_dir.replace("\\", "/").split("/")
        path  = ""
        for part in parts:
            if not part:
                path = "/"
                continue
            path = f"{path}/{part}" if path and path != "/" else f"/{part}" if path == "/" else part
            try:
                sftp.stat(path)
            except FileNotFoundError:
                sftp.mkdir(path)

    def _sftp_put_recursive(self, sftp, local: Path, remote: str):
        """Upload local file or directory tree to remote path via SFTP."""
        self._sftp_mkdir_p(sftp, remote)
        if local.is_file():
            sftp.put(str(local), f"{remote}/{local.name}")
        else:
            for item in local.iterdir():
                r_sub = f"{remote}/{item.name}"
                if item.is_dir():
                    self._sftp_mkdir_p(sftp, r_sub)
                    self._sftp_put_recursive(sftp, item, r_sub)
                else:
                    sftp.put(str(item), r_sub)

    def _sftp_get_recursive(self, sftp, remote: str, local: Path):
        """Download remote directory tree to local path via SFTP."""
        local.mkdir(parents=True, exist_ok=True)
        for entry in sftp.listdir_attr(remote):
            r_path = f"{remote}/{entry.filename}"
            l_path = local / entry.filename
            import stat
            if stat.S_ISDIR(entry.st_mode):
                self._sftp_get_recursive(sftp, r_path, l_path)
            else:
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
        dest  = self._remote_addr(cloud_folder)
        rsync = subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        if rsync:
            ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
            cmd = ["rsync", "-az", "--mkpath", "-e", ssh_cmd,
                   str(lp) + ("/" if lp.is_dir() else ""),
                   dest]
        else:
            cmd = ["scp"] + self._ssh_opts(batch_mode=True) + ["-r", str(lp), dest]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Transfer failed (exit {result.returncode})")

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
        src   = self._remote_addr(cloud_folder)
        rsync = subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        if rsync:
            ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
            cmd = ["rsync", "-az", "-e", ssh_cmd, src + "/", str(lp)]
        else:
            cmd = ["scp"] + self._ssh_opts(batch_mode=True) + ["-r", src, str(lp)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"Transfer failed (exit {result.returncode})")

    # ── Direct machine-to-machine sync (absolute paths, rsync --update) ────────

    def push_path(self, local_path: str | Path, remote_path: str):
        """Push local_path to absolute remote_path on the remote machine."""
        lp = Path(local_path)
        has_rsync = subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        if self.ssh_password or not has_rsync:
            if not PARAMIKO_AVAILABLE:
                raise RuntimeError("paramiko is required. Run: pip install paramiko")
            client, sftp = self._sftp_client()
            try:
                self._sftp_mkdir_p(sftp, remote_path)
                self._sftp_put_recursive(sftp, lp, remote_path)
            finally:
                sftp.close()
                client.close()
            return
        ssh_cmd = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
        src_arg  = str(lp) + ("/" if lp.is_dir() else "")
        dest_arg = f"{self.username}@{self.ip}:{remote_path}"
        cmd = ["rsync", "-avz", "--update", "-e", ssh_cmd, src_arg, dest_arg]
        result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rsync push failed (exit {result.returncode})")

    def pull_path(self, remote_path: str, local_path: str | Path):
        """Pull from absolute remote_path on the remote machine to local_path."""
        lp = Path(local_path)
        has_rsync = subprocess.run(["which", "rsync"], capture_output=True).returncode == 0
        if self.ssh_password or not has_rsync:
            if not PARAMIKO_AVAILABLE:
                raise RuntimeError("paramiko is required. Run: pip install paramiko")
            client, sftp = self._sftp_client()
            try:
                self._sftp_get_recursive(sftp, remote_path, lp)
            finally:
                sftp.close()
                client.close()
            return
        lp.mkdir(parents=True, exist_ok=True)
        ssh_cmd  = "ssh " + " ".join(self._ssh_opts(batch_mode=True))
        src_arg  = f"{self.username}@{self.ip}:{remote_path}/"
        cmd = ["rsync", "-avz", "--update", "-e", ssh_cmd, src_arg, str(lp)]
        result = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rsync pull failed (exit {result.returncode})")


# ── Background thread for direct machine-to-machine sync ─────────────────────

class DirectSyncWorkerThread(QThread):
    """Runs a push_path / pull_path operation in a background thread."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, sync_obj: LocalNetworkSync, operation: str,
                 local_path: str, remote_path: str):
        super().__init__()
        self.sync_obj    = sync_obj
        self.operation   = operation   # "push" or "pull"
        self.local_path  = local_path
        self.remote_path = remote_path

    def run(self):
        try:
            verb = "Pushing to" if self.operation == "push" else "Pulling from"
            self.progress.emit(f"{verb} remote machine…")
            if self.operation == "push":
                self.sync_obj.push_path(self.local_path, self.remote_path)
                self.finished.emit(True, "Push complete.")
            else:
                self.sync_obj.pull_path(self.remote_path, self.local_path)
                self.finished.emit(True, "Pull complete.")
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────

class SyncApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Sync Tool")
        self.setGeometry(200, 200, 1000, 700)

        self.settings_file  = self.get_settings_file_path()
        self.game_defaults  = {}
        self.previous_paths = {}
        self.scanned_hosts  = []  # list of (ip, os_type, label, mac, is_local)
        self._current_dest_mac = ""   # MAC of currently-selected destination
        self._current_dest_ip  = ""   # IP  of currently-selected destination
        self.local_os       = "Linux" if platform.system() != "Windows" else "Windows"
        self.local_interfaces, self.local_ips, self.local_macs = self._get_local_network_identity()

        self.scan_active = False
        self.sync_active = False
        self.scan_performed = False
        self._loading   = False

        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.setup_darker_theme()
        self.init_ui()
        self.load_game_defaults()
        self.load_settings()
        self._apply_local_os_source_path()

        # ── Auto-save on any path/game/direction change ────────────────────────
        self.game_dropdown.currentIndexChanged.connect(self._on_game_or_direction_changed)
        self.sync_direction_dropdown.currentIndexChanged.connect(self._on_game_or_direction_changed)
        self.source_path.editingFinished.connect(self.save_settings)
        self.dest_path.editingFinished.connect(self.save_settings)
        self.cloud_folder_input.editingFinished.connect(self.save_settings)

        self.scan_active = False
        self.sync_active = False
        self.scan_performed = False
        self._loading   = False
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(60_000)
        self.scan_timer.timeout.connect(self.on_scan_timer_timeout)
        self.scan_timer.start()
        if self._should_auto_scan_network():
            self.start_network_scan()

        self._last_game_selected = self.game_dropdown.currentText()

        # ── Cloud state ───────────────────────────────────────────────────────
        self.gdrive_sync:       GoogleDriveSync    | None = None
        self.dropbox_sync:      DropboxSync        | None = None
        self.local_network_sync: LocalNetworkSync  | None = None
        self.lm_password:       str               = ""
        self.cloud_worker:      CloudWorkerThread  | None = None

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
        return ip in self.local_ips or (normalized_mac and normalized_mac in self.local_macs)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def setup_darker_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,      QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText,  Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base,        QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.Text,        Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button,      QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText,  Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Highlight,   QColor(87, 134, 193))
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
        header_widget.setStyleSheet("background-color: #2d2d2d; border-bottom: 1px solid #444;")
        header_layout = QHBoxLayout(header_widget)
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
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)

        description_label = QLabel("Select your game, choose the destination machine, and start syncing your game files effortlessly.")
        description_label.setStyleSheet("font-size: 12px; color: gray;")
        content_layout.addWidget(description_label, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # ── Local machine info ────────────────────────────────────────────────
        self.local_os_label = QLabel(f"Local machine OS: {self.local_os}")
        self.local_os_label.setStyleSheet("font-size: 11px; color: lightblue;")
        content_layout.addWidget(self.local_os_label)

        # ── Game Selection ────────────────────────────────────────────────────
        self.select_game_label = QLabel("Select Game:")
        content_layout.addWidget(self.select_game_label)

        self.game_dropdown = QComboBox()
        content_layout.addWidget(self.game_dropdown)

        # ── Cloud Storage accordion ───────────────────────────────────────────
        self.cloud_enabled_checkbox = QCheckBox("Enable Cloud Storage (middle-man sync)")
        self.cloud_enabled_checkbox.setStyleSheet("font-size: 11px; color: #9fd3ff; font-weight: bold;")
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

        # Provider row
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Provider:"))
        self.cloud_provider_group = QButtonGroup(self)
        for idx, name in enumerate(["Google Drive", "Dropbox", "Both (GDrive+Dropbox)", "Local Network Machine"]):
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

        auth_method_row = QHBoxLayout()
        auth_method_row.addWidget(QLabel("Auth method:"))
        self.gd_auth_group = QButtonGroup(self)
        self.gd_oauth_rb    = QRadioButton("OAuth (browser)")
        self.gd_manual_rb   = QRadioButton("Client ID + Secret")
        self.gd_oauth_rb.setChecked(True)
        self.gd_auth_group.addButton(self.gd_oauth_rb, 0)
        self.gd_auth_group.addButton(self.gd_manual_rb, 1)
        self.gd_auth_group.idToggled.connect(self.on_gd_auth_method_changed)
        auth_method_row.addWidget(self.gd_oauth_rb)
        auth_method_row.addWidget(self.gd_manual_rb)
        auth_method_row.addStretch()
        gd_layout.addLayout(auth_method_row)

        self.gd_credentials_widget = QWidget()
        gd_cred_layout = QVBoxLayout()
        gd_cred_layout.setContentsMargins(0, 0, 0, 0)
        gd_cid_row = QHBoxLayout()
        gd_cid_row.addWidget(QLabel("Client ID:"))
        self.gd_client_id_input = QLineEdit()
        self.gd_client_id_input.setPlaceholderText("Paste Google OAuth client ID")
        gd_cid_row.addWidget(self.gd_client_id_input)
        gd_cred_layout.addLayout(gd_cid_row)
        gd_csec_row = QHBoxLayout()
        gd_csec_row.addWidget(QLabel("Client Secret:"))
        self.gd_client_secret_input = QLineEdit()
        self.gd_client_secret_input.setPlaceholderText("Paste Google OAuth client secret")
        self.gd_client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        gd_csec_row.addWidget(self.gd_client_secret_input)
        gd_cred_layout.addLayout(gd_csec_row)
        self.gd_credentials_widget.setLayout(gd_cred_layout)
        self.gd_credentials_widget.setVisible(False)
        gd_layout.addWidget(self.gd_credentials_widget)

        gd_btn_row = QHBoxLayout()
        self.gd_connect_btn = QPushButton("Connect Google Drive")
        self.gd_connect_btn.setFixedWidth(180)
        self.gd_connect_btn.clicked.connect(self.connect_gdrive)
        gd_btn_row.addWidget(self.gd_connect_btn)
        self.gd_status_label = QLabel("Not connected")
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

        db_key_row = QHBoxLayout()
        db_key_row.addWidget(QLabel("App Key:"))
        self.db_app_key_input = QLineEdit()
        self.db_app_key_input.setPlaceholderText("Dropbox App Key")
        db_key_row.addWidget(self.db_app_key_input)
        db_layout.addLayout(db_key_row)

        db_sec_row = QHBoxLayout()
        db_sec_row.addWidget(QLabel("App Secret:"))
        self.db_app_secret_input = QLineEdit()
        self.db_app_secret_input.setPlaceholderText("Dropbox App Secret")
        self.db_app_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        db_sec_row.addWidget(self.db_app_secret_input)
        db_layout.addLayout(db_sec_row)

        db_auth_row = QHBoxLayout()
        self.db_get_url_btn = QPushButton("1. Open Auth URL")
        self.db_get_url_btn.setFixedWidth(140)
        self.db_get_url_btn.clicked.connect(self.dropbox_open_auth_url)
        db_auth_row.addWidget(self.db_get_url_btn)
        db_auth_row.addSpacing(8)
        self.db_code_input = QLineEdit()
        self.db_code_input.setPlaceholderText("2. Paste authorisation code here")
        db_auth_row.addWidget(self.db_code_input)
        self.db_finish_btn = QPushButton("3. Finish Auth")
        self.db_finish_btn.setFixedWidth(100)
        self.db_finish_btn.clicked.connect(self.dropbox_finish_auth)
        db_auth_row.addWidget(self.db_finish_btn)
        db_layout.addLayout(db_auth_row)

        db_status_row = QHBoxLayout()
        self.db_status_label = QLabel("Not connected")
        self.db_status_label.setStyleSheet("font-size: 10px; color: gray;")
        db_status_row.addWidget(self.db_status_label)
        db_status_row.addStretch()
        db_layout.addLayout(db_status_row)

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
        self.lm_remote_path_input.setPlaceholderText("e.g.  /home/pi/GameSync")
        lm_path_row.addWidget(self.lm_remote_path_input)
        lm_layout.addLayout(lm_path_row)

        lm_key_row = QHBoxLayout()
        lm_key_label = QLabel("SSH key:")
        lm_key_label.setFixedWidth(80)
        lm_key_row.addWidget(lm_key_label)
        self.lm_ssh_key_input = QLineEdit()
        self.lm_ssh_key_input.setPlaceholderText("(optional) path to private key, e.g. ~/.ssh/id_rsa")
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
        dest_ssh_layout.setContentsMargins(0, 4, 0, 0)
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

        # ── Sync Direction ────────────────────────────────────────────────────
        self.sync_direction_label = QLabel("Sync Direction:")
        content_layout.addWidget(self.sync_direction_label)

        self.sync_direction_dropdown = QComboBox()
        self.sync_direction_dropdown.addItems([
            "Linux ↔ Linux",
            "Linux ↔ Windows",
            "Windows ↔ Linux",
            "Windows ↔ Windows",
        ])
        content_layout.addWidget(self.sync_direction_dropdown)

        # ── Sync Button ───────────────────────────────────────────────────────
        self.sync_button = QPushButton("⬆  Push to Dest")
        self.sync_button.setStyleSheet("background-color: #3a5a8a; color: white;")
        self.sync_button.clicked.connect(self.start_sync)

        self.pull_dest_btn = QPushButton("⬇  Pull from Dest")
        self.pull_dest_btn.setStyleSheet("background-color: #3a6a4a; color: white;")
        self.pull_dest_btn.setVisible(False)
        self.pull_dest_btn.clicked.connect(self.pull_from_dest)

        self.direct_sync_status_label = QLabel("")
        self.direct_sync_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
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

        warning_label = QLabel("Syncing large game files may take time. Please be patient and do not interrupt the process.")
        warning_label.setStyleSheet("font-size: 10px; color: orange;")
        warning_label.setWordWrap(False)
        warning_label.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
        content_layout.addWidget(warning_label, alignment=Qt.AlignmentFlag.AlignCenter)

        sync_btn_row = QHBoxLayout()
        sync_btn_row.addWidget(self.sync_button)
        sync_btn_row.addWidget(self.pull_dest_btn)
        sync_btn_row.addWidget(self.push_cloud_btn)
        sync_btn_row.addWidget(self.pull_cloud_btn)
        content_layout.addLayout(sync_btn_row)
        content_layout.addWidget(self.cloud_op_status_label, alignment=Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self.direct_sync_status_label, alignment=Qt.AlignmentFlag.AlignCenter)

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
        outer_layout.addWidget(scroll_area)
        self.setCentralWidget(outer_widget)

    # ── Cloud UI callbacks ────────────────────────────────────────────────────

    def toggle_cloud_section(self, enabled: bool):
        self.cloud_section.setVisible(enabled)
        # When cloud is active, hide direct-machine buttons; when not, hide cloud buttons
        cloud_on = enabled
        dest_selected = bool(self._current_dest_mac or self._current_dest_ip)
        self.sync_button.setVisible(not cloud_on)
        self.pull_dest_btn.setVisible(not cloud_on and dest_selected)
        self.push_cloud_btn.setVisible(cloud_on)
        self.pull_cloud_btn.setVisible(cloud_on)
        self.cloud_op_status_label.setVisible(cloud_on)
        self.direct_sync_status_label.setVisible(not cloud_on)
        # Hide sync direction and destination path when cloud storage is enabled.
        self.sync_direction_label.setVisible(not cloud_on)
        self.sync_direction_dropdown.setVisible(not cloud_on)
        self.dest_machine_widget.setVisible(not cloud_on)
        self.dest_ssh_section.setVisible(not cloud_on and dest_selected)
        self.dest_label.setVisible(not cloud_on)
        self.dest_path.setVisible(not cloud_on)
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
            "/GameSync/<GameName>/" if not is_local else
            "(sub-folder appended to remote path above, e.g. Zomboid)"
        )
        self.cloud_folder_row.setVisible(not is_local)
        if btn_id == 3 and self.lm_host_dropdown.count() <= 1 and not getattr(self, 'scan_active', False):
            self.start_network_scan()
        self._refresh_local_machine_scan_state()

    def on_gd_auth_method_changed(self, btn_id: int, checked: bool):
        if not checked:
            return
        self.gd_credentials_widget.setVisible(btn_id == 1)  # 1 = manual creds

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
        self.lm_scan_progress.setVisible(self.scan_active and self.local_machine_section.isVisible())

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
            remote_os = self._remote_os_from_direction(self.sync_direction_dropdown.currentText())
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
            self, "Select SSH Private Key",
            str(Path.home() / ".ssh"),
            "All files (*)"
        )
        if path:
            self.dest_ssh_key_input.setText(path)

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
        ip  = self._current_dest_ip
        usr = self.dest_ssh_user_input.text().strip()
        if not ip or not usr:
            return None
        port_txt = self.dest_ssh_port_input.text().strip()
        try:
            port = int(port_txt) if port_txt else 22
        except ValueError:
            port = 22
        key      = self.dest_ssh_key_input.text().strip()
        password = getattr(self, "dest_password", "")
        return LocalNetworkSync(ip, usr, "/", port, key, password)

    def _test_dest_connection(self):
        usr = self.dest_ssh_user_input.text().strip()
        if not self._current_dest_ip or not usr:
            self.dest_ssh_status_label.setText("Select a destination machine and enter username first.")
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
        self.dest_ssh_status_label.setText("Testing…")
        self.dest_ssh_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.dest_ssh_progress.setVisible(True)

        import threading  # noqa: PLC0415
        def _run():
            try:
                ok, msg = sync_obj.test_connection()
            except Exception as exc:
                ok, msg = False, str(exc)
            color = "#7ed6a9" if ok else "red"
            QTimer.singleShot(0, lambda: self._on_dest_test_done(ok, msg, color))
        threading.Thread(target=_run, daemon=True).start()

    def _on_dest_test_done(self, ok: bool, msg: str, color: str):
        self.dest_ssh_test_btn.setEnabled(True)
        self.dest_ssh_progress.setVisible(False)
        self.dest_ssh_status_label.setText(("✓ " if ok else "✗ ") + msg[:70])
        self.dest_ssh_status_label.setStyleSheet(f"font-size: 10px; color: {color};")
        if ok:
            self.save_settings()

    # ── Direct machine-to-machine sync ────────────────────────────────────────

    def _start_direct_sync(self, operation: str):
        """Common launcher for push/pull between this machine and the destination."""
        src  = self.source_path.text().strip()
        dest = self.dest_path.text().strip()
        if not src or not dest:
            self.direct_sync_status_label.setText("Source Path and Destination Path must both be set.")
            self.direct_sync_status_label.setStyleSheet("font-size: 10px; color: orange;")
            self.direct_sync_status_label.setVisible(True)
            return

        sync_obj = self._build_dest_sync()
        if sync_obj is None:
            self.direct_sync_status_label.setText(
                "Fill in Destination SSH Username (and credentials) first."
            )
            self.direct_sync_status_label.setStyleSheet("font-size: 10px; color: orange;")
            self.direct_sync_status_label.setVisible(True)
            return

        if not sync_obj.ssh_key and not sync_obj.ssh_password:
            self._set_dest_password()
            sync_obj = self._build_dest_sync()
            if not getattr(self, "dest_password", ""):
                return   # user cancelled password entry

        local_path  = src  if operation == "push" else dest
        remote_path = dest if operation == "push" else src

        self.sync_button.setEnabled(False)
        self.pull_dest_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.direct_sync_status_label.setVisible(True)
        self.save_settings()

        self._direct_worker = DirectSyncWorkerThread(sync_obj, operation, local_path, remote_path)
        self._direct_worker.progress.connect(self._on_direct_sync_progress)
        self._direct_worker.finished.connect(self._on_direct_sync_finished)
        self._direct_worker.start()

    def _on_direct_sync_progress(self, msg: str):
        self.direct_sync_status_label.setText(msg)

    def _on_direct_sync_finished(self, ok: bool, msg: str):
        self.sync_button.setEnabled(True)
        self.pull_dest_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if ok else 0)
        QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False))
        color = "#7ed6a9" if ok else "red"
        self.direct_sync_status_label.setText(("✓ " if ok else "✗ ") + msg)
        self.direct_sync_status_label.setStyleSheet(f"font-size: 10px; color: {color};")

    def pull_from_dest(self):
        self._start_direct_sync("pull")

    # ── Google Drive auth ─────────────────────────────────────────────────────

    def connect_gdrive(self):
        client_id     = self.gd_client_id_input.text().strip()
        client_secret = self.gd_client_secret_input.text().strip()

        if not GDRIVE_AVAILABLE:
            self.gd_status_label.setText("Missing: install google-api-python-client google-auth-oauthlib")
            self.gd_status_label.setStyleSheet("font-size: 10px; color: red;")
            return

        # For OAuth mode the user may leave cred fields blank (env creds)
        if self.gd_manual_rb.isChecked() and (not client_id or not client_secret):
            self.gd_status_label.setText("Enter Client ID + Secret first.")
            self.gd_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return

        self.gd_connect_btn.setEnabled(False)
        self.gd_status_label.setText("Opening browser…")
        self.gd_status_label.setStyleSheet("font-size: 10px; color: lightgray;")

        saved         = self.previous_paths.get("gdrive_token")
        self.gdrive_sync = GoogleDriveSync(client_id, client_secret, saved)

        if self.gdrive_sync.is_authenticated():
            self._on_gdrive_connected()
            return

        # Run OAuth in a thread so the UI stays responsive
        import threading  # noqa: PLC0415
        def _run():
            try:
                token = self.gdrive_sync.authenticate()
                self.previous_paths["gdrive_token"]          = token
                self.previous_paths["gdrive_client_id"]      = client_id
                self.previous_paths["gdrive_client_secret"]  = client_secret
                self.save_settings()
                # Signal back to main thread
                QTimer.singleShot(0, self._on_gdrive_connected)
            except Exception as exc:
                msg = str(exc)
                QTimer.singleShot(0, lambda: self._on_gdrive_error(msg))
        threading.Thread(target=_run, daemon=True).start()

    def _on_gdrive_connected(self):
        self.gd_connect_btn.setEnabled(True)
        self.gd_status_label.setText("✓ Connected")
        self.gd_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
        # Refresh token in case it was refreshed
        if self.gdrive_sync:
            new_token = self.gdrive_sync.refreshed_token()
            if new_token:
                self.previous_paths["gdrive_token"] = new_token
                self.save_settings()

    def _on_gdrive_error(self, msg: str):
        self.gd_connect_btn.setEnabled(True)
        self.gd_status_label.setText(f"Error: {msg[:60]}")
        self.gd_status_label.setStyleSheet("font-size: 10px; color: red;")

    # ── Dropbox auth ──────────────────────────────────────────────────────────

    def dropbox_open_auth_url(self):
        if not DROPBOX_AVAILABLE:
            self.db_status_label.setText("Missing: install dropbox")
            self.db_status_label.setStyleSheet("font-size: 10px; color: red;")
            return
        app_key    = self.db_app_key_input.text().strip()
        app_secret = self.db_app_secret_input.text().strip()
        if not app_key or not app_secret:
            self.db_status_label.setText("Enter App Key + Secret first.")
            self.db_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return
        try:
            self.dropbox_sync = DropboxSync(app_key, app_secret)
            url = self.dropbox_sync.get_auth_url()
            webbrowser.open(url)
            self.db_status_label.setText("Browser opened — paste the code and click Finish Auth.")
            self.db_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        except Exception as exc:
            self.db_status_label.setText(str(exc)[:80])
            self.db_status_label.setStyleSheet("font-size: 10px; color: red;")

    def dropbox_finish_auth(self):
        code = self.db_code_input.text().strip()
        if not code:
            self.db_status_label.setText("Paste the authorisation code first.")
            return
        if not self.dropbox_sync:
            self.db_status_label.setText("Click 'Open Auth URL' first.")
            return
        try:
            tokens = self.dropbox_sync.finish_auth(code)
            self.previous_paths["dropbox_access_token"]  = tokens["access_token"]
            self.previous_paths["dropbox_refresh_token"] = tokens.get("refresh_token", "")
            self.previous_paths["dropbox_app_key"]       = self.db_app_key_input.text().strip()
            self.previous_paths["dropbox_app_secret"]    = self.db_app_secret_input.text().strip()
            self.save_settings()
            self.db_status_label.setText("✓ Connected")
            self.db_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self.db_code_input.clear()
        except Exception as exc:
            self.db_status_label.setText(f"Error: {exc}")
            self.db_status_label.setStyleSheet("font-size: 10px; color: red;")

    # ── Cloud push / pull ─────────────────────────────────────────────────────

    def _active_cloud_sync_objects(self) -> list:
        """Return whichever cloud sync objects are ready based on selected provider."""
        btn_id  = self.cloud_provider_group.checkedId()  # 0=GDrive, 1=Dropbox, 2=Both, 3=Local
        objects = []
        if btn_id in (0, 2) and self.gdrive_sync and self.gdrive_sync.is_authenticated():
            objects.append(("Google Drive", self.gdrive_sync))
        if btn_id in (1, 2) and self.dropbox_sync and self.dropbox_sync.is_authenticated():
            objects.append(("Dropbox", self.dropbox_sync))
        if btn_id == 3 and self.local_network_sync and self.local_network_sync.is_authenticated():
            objects.append(("Local Machine", self.local_network_sync))
        return objects

    def _cloud_folder_for_game(self) -> str:
        folder = self.cloud_folder_input.text().strip()
        game   = self.game_dropdown.currentText() or "Game"
        if not folder:
            # Local machine mode: just the game name as a sub-folder under remote_base
            if self.cloud_provider_group.checkedId() == 3:
                return game
            folder = f"/GameSync/{game}/"
        return folder

    def push_to_cloud(self):
        local_path = self.source_path.text().strip()
        if not local_path:
            self.cloud_op_status_label.setText("Set a Source Path first.")
            return
        cloud_syncs = self._active_cloud_sync_objects()
        if not cloud_syncs:
            self.cloud_op_status_label.setText("No authenticated cloud provider available.")
            return
        self._run_cloud_op("upload", cloud_syncs, local_path)

    def pull_from_cloud(self):
        local_path = self.dest_path.text().strip() or self.source_path.text().strip()
        if not local_path:
            self.cloud_op_status_label.setText("Set a Destination Path first.")
            return
        cloud_syncs = self._active_cloud_sync_objects()
        if not cloud_syncs:
            self.cloud_op_status_label.setText("No authenticated cloud provider available.")
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
        self.progress_bar.setRange(0, 0)

        self.cloud_worker = CloudWorkerThread(operation, sync_obj, local_path, cloud_folder)
        self.cloud_worker.progress.connect(self.cloud_op_status_label.setText)
        self.cloud_worker.finished.connect(
            lambda ok, msg: self._on_cloud_op_finished(ok, msg, cloud_syncs[1:], operation, local_path, cloud_folder)
        )
        self.cloud_worker.start()

    def _on_cloud_op_finished(self, ok: bool, msg: str, remaining: list,
                               operation: str, local_path: str, cloud_folder: str):
        if not ok:
            self.cloud_op_status_label.setText(f"Error: {msg[:80]}")
            self.cloud_op_status_label.setStyleSheet("font-size: 10px; color: red;")
            self._reset_cloud_buttons()
            return

        if remaining:
            # Chain to next provider
            name, sync_obj = remaining[0]
            self.cloud_op_status_label.setText(f"{operation.title()}ing via {name}…")
            self.cloud_worker = CloudWorkerThread(operation, sync_obj, local_path, cloud_folder)
            self.cloud_worker.progress.connect(self.cloud_op_status_label.setText)
            self.cloud_worker.finished.connect(
                lambda ok2, msg2: self._on_cloud_op_finished(ok2, msg2, remaining[1:], operation, local_path, cloud_folder)
            )
            self.cloud_worker.start()
        else:
            self.cloud_op_status_label.setText(f"✓ {msg}")
            self.cloud_op_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")
            self._reset_cloud_buttons()

    def _reset_cloud_buttons(self):
        self.push_cloud_btn.setEnabled(True)
        self.pull_cloud_btn.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)

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
            self, "Select SSH Private Key",
            str(Path.home() / ".ssh"),
            "All files (*)"
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
        ip       = self.previous_paths.get("lm_ip", "")
        username = self.lm_username_input.text().strip()
        rpath    = self.lm_remote_path_input.text().strip()
        port_txt = self.lm_port_input.text().strip()
        key      = self.lm_ssh_key_input.text().strip()

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
        self.local_network_sync = LocalNetworkSync(ip, username, rpath, port, key, password)
        return True

    def _test_local_machine_connection(self):
        if not self._build_local_network_sync():
            self.lm_status_label.setText("Fill in Machine, Username, and Remote Path first.")
            self.lm_status_label.setStyleSheet("font-size: 10px; color: orange;")
            return

        if not self.local_network_sync.ssh_key and not self.local_network_sync.ssh_password:
            password = self._request_local_machine_password()
            if not password:
                self.lm_status_label.setText("SSH password required or provide an SSH key.")
                self.lm_status_label.setStyleSheet("font-size: 10px; color: orange;")
                return
            self.local_network_sync.ssh_password = password

        self.lm_test_btn.setEnabled(False)
        self.lm_status_label.setText("Testing…")
        self.lm_status_label.setStyleSheet("font-size: 10px; color: lightgray;")
        self.lm_scan_progress.setVisible(True)

        import threading  # noqa: PLC0415
        obj = self.local_network_sync
        def _run():
            try:
                ok, msg = obj.test_connection()
            except Exception as exc:
                ok, msg = False, str(exc)
            color = "#7ed6a9" if ok else "red"
            QTimer.singleShot(0, lambda: self._on_lm_test_done(ok, msg, color))
        threading.Thread(target=_run, daemon=True).start()

    def _on_lm_test_done(self, ok: bool, msg: str, color: str):
        self.lm_test_btn.setEnabled(True)
        self.lm_scan_progress.setVisible(False)
        self.lm_status_label.setText(("✓ " if ok else "✗ ") + msg[:70])
        self.lm_status_label.setStyleSheet(f"font-size: 10px; color: {color};")
        if ok:
            self.save_settings()

    # ── Network scan ──────────────────────────────────────────────────────────

    def on_scan_timer_timeout(self):
        if self.sync_active or not self._should_auto_scan_network():
            return
        self.start_network_scan()

    def _should_auto_scan_network(self) -> bool:
        if self._current_dest_mac:
            return False
        if self.scan_dropdown.count() <= 1 and not self.scan_performed:
            return True
        return False

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
            prepared_hosts.append((ip, os_type, display_label, normalized_mac, is_local))
            seen_hosts.add(host_key)

        for interface in self.local_interfaces:
            ip = interface["ip"]
            mac = (interface["mac"] or "").lower()
            host_key = (ip, mac)
            if host_key in seen_hosts:
                continue

            hostname = socket.gethostname()
            iface_name = interface["iface"]
            display_label = f"{ip}  ({hostname} / {iface_name})  [{self.local_os}] (this machine)"
            prepared_hosts.append((ip, self.local_os, display_label, mac, True))
            seen_hosts.add(host_key)

        self.scanned_hosts = prepared_hosts

        last_dest_mac = self.previous_paths.get("last_dest_mac", "").lower()
        last_dest_ip  = self.previous_paths.get("last_dest_ip",  "")
        auto_select_index = 0

        for index, (_ip, _os, label, _mac, is_local) in enumerate(self.scanned_hosts, start=1):
            self.scan_dropdown.addItem(label)
            if is_local:
                self.scan_dropdown.setItemData(index, QColor("orange"), Qt.ItemDataRole.ForegroundRole)
            # Check if this host matches the last-used destination
            if not is_local and auto_select_index == 0:
                if (last_dest_mac and _mac and _mac.lower() == last_dest_mac) or \
                   (last_dest_ip and _ip == last_dest_ip):
                    auto_select_index = index

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
                self.scan_status_label.text() + "  (last destination auto-selected)")

    def on_destination_selected(self, index):
        """Auto-set sync direction and paths when a scanned machine is selected."""
        if index <= 0 or index > len(self.scanned_hosts):
            self._current_dest_mac = ""
            self._current_dest_ip  = ""
            self.dest_ssh_section.setVisible(False)
            self.pull_dest_btn.setVisible(False)
            self.direct_sync_status_label.setVisible(False)
            self._update_scan_button_label()
            return

        dest_ip, remote_os, _label, dest_mac, is_local = self.scanned_hosts[index - 1]
        if is_local:
            self.scan_status_label.setText("This entry is the current machine. Choose another destination.")
            self._update_scan_button_label()
            return

        self._current_dest_mac = dest_mac
        self._current_dest_ip  = dest_ip

        # Show destination SSH credentials section
        self.dest_ssh_section.setVisible(True)
        self.pull_dest_btn.setVisible(True)
        self.direct_sync_status_label.setVisible(True)

        # Load saved credentials for this destination machine
        saved_creds = self.previous_paths.get("dest_machine_creds", {}).get(dest_mac, {})
        if saved_creds.get("username"):
            self.dest_ssh_user_input.setText(saved_creds["username"])
        elif not self.dest_ssh_user_input.text():
            # Suggest username based on detected OS
            self.dest_ssh_user_input.setPlaceholderText(
                "pi / user" if remote_os == "Linux" else "Administrator"
            )
        if saved_creds.get("ssh_key"):
            self.dest_ssh_key_input.setText(saved_creds["ssh_key"])
        if saved_creds.get("port"):
            self.dest_ssh_port_input.setText(str(saved_creds["port"]))
        # Reset password button appearance (password is never persisted)
        self.dest_password = ""
        self.dest_ssh_pass_btn.setText("Set Password")
        self.dest_ssh_pass_btn.setStyleSheet("")

        # Update in-memory record of last destination before setting direction
        # and paths so that _game_machine_key() returns the correct key when
        # update_paths() / save_settings() are called below.
        self.previous_paths["last_dest_mac"] = dest_mac
        self.previous_paths["last_dest_ip"]  = dest_ip

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
                key   = f"{game or ''}__"
                saved = self.previous_paths.get("game_machine_paths", {}).get(key, {})
                self.source_path.setText(saved.get("source_path",
                    self.previous_paths.get("source_path", "")))
                self.dest_path.setText(saved.get("dest_path",
                    self.previous_paths.get("dest_path", "")))

                # ── Cloud settings ────────────────────────────────────────────
                cloud_enabled = self.previous_paths.get("cloud_enabled", False)
                self.cloud_enabled_checkbox.setChecked(cloud_enabled)

                provider_idx = self.previous_paths.get("cloud_provider_idx", 0)
                btn = self.cloud_provider_group.button(provider_idx)
                if btn:
                    btn.setChecked(True)

                self.cloud_folder_input.setText(self.previous_paths.get("cloud_folder", ""))
                self._refresh_cloud_folder_default()
                self._last_game_selected = self.game_dropdown.currentText()

                # Google Drive
                self.gd_client_id_input.setText(self.previous_paths.get("gdrive_client_id", ""))
                self.gd_client_secret_input.setText(self.previous_paths.get("gdrive_client_secret", ""))
                if self.previous_paths.get("gdrive_token"):
                    cid  = self.previous_paths.get("gdrive_client_id", "")
                    csec = self.previous_paths.get("gdrive_client_secret", "")
                    if GDRIVE_AVAILABLE and cid and csec:
                        self.gdrive_sync = GoogleDriveSync(cid, csec, self.previous_paths["gdrive_token"])
                        if self.gdrive_sync.is_authenticated():
                            self.gd_status_label.setText("✓ Connected")
                            self.gd_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")

                # Dropbox
                self.db_app_key_input.setText(self.previous_paths.get("dropbox_app_key", ""))
                self.db_app_secret_input.setText(self.previous_paths.get("dropbox_app_secret", ""))
                if self.previous_paths.get("dropbox_access_token"):
                    self.dropbox_sync = DropboxSync(
                        self.previous_paths.get("dropbox_app_key", ""),
                        self.previous_paths.get("dropbox_app_secret", ""),
                        self.previous_paths.get("dropbox_access_token", ""),
                        self.previous_paths.get("dropbox_refresh_token", ""),
                    )
                    if self.dropbox_sync.is_authenticated():
                        self.db_status_label.setText("✓ Connected")
                        self.db_status_label.setStyleSheet("font-size: 10px; color: #7ed6a9;")

                # Local network machine
                self.lm_username_input.setText(self.previous_paths.get("lm_username", ""))
                self.lm_remote_path_input.setText(self.previous_paths.get("lm_remote_path", ""))
                self.lm_port_input.setText(self.previous_paths.get("lm_port", "22"))
                self.lm_ssh_key_input.setText(self.previous_paths.get("lm_ssh_key", ""))
                if self.previous_paths.get("lm_ip") and self.previous_paths.get("lm_username"):
                    self.lm_status_label.setText(
                        f"Saved: {self.previous_paths['lm_ip']} "
                        f"({self.previous_paths['lm_username']})"
                    )
                    self.lm_status_label.setStyleSheet("font-size: 10px; color: lightgray;")

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
        selected_game      = self.game_dropdown.currentText()
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
        key   = self._game_machine_key()
        saved = self.previous_paths.get("game_machine_paths", {}).get(key, {})
        src   = saved.get("source_path") or src
        dst   = saved.get("dest_path")   or dst

        self.source_path.setText(src)
        self.dest_path.setText(dst)

    def save_settings(self):
        if getattr(self, "_loading", False):
            return

        # Preserve all previously loaded keys (cloud tokens, etc.) then overlay
        settings = dict(self.previous_paths)

        settings["game"]           = self.game_dropdown.currentText()
        settings["sync_direction"] = self.sync_direction_dropdown.currentText()

        # ── Per-game + destination-machine path persistence ───────────────────
        key = self._game_machine_key()
        game_machine_paths = settings.get("game_machine_paths", {})
        game_machine_paths[key] = {
            "source_path":    self.source_path.text(),
            "dest_path":      self.dest_path.text(),
            "sync_direction": self.sync_direction_dropdown.currentText(),
        }
        settings["game_machine_paths"] = game_machine_paths

        # ── Game-specific cloud folder persistence ─────────────────────────────
        game_cloud_folders = settings.get("game_cloud_folders", {})
        game_cloud_folders[self.game_dropdown.currentText() or "__unknown__"] = self.cloud_folder_input.text()
        settings["game_cloud_folders"] = game_cloud_folders

        # ── Destination machine SSH credentials (username + key, no password) ──
        if self._current_dest_mac:
            dest_machine_creds = settings.get("dest_machine_creds", {})
            dest_machine_creds[self._current_dest_mac] = {
                "username": self.dest_ssh_user_input.text(),
                "ssh_key":  self.dest_ssh_key_input.text(),
                "port":     self.dest_ssh_port_input.text(),
            }
            settings["dest_machine_creds"] = dest_machine_creds

        # ── Last destination machine ──────────────────────────────────────────
        if self._current_dest_mac:
            settings["last_dest_mac"] = self._current_dest_mac
            settings["last_dest_ip"]  = self._current_dest_ip

        # ── Cloud UI state ────────────────────────────────────────────────────
        settings["cloud_enabled"]      = self.cloud_enabled_checkbox.isChecked()
        settings["cloud_provider_idx"] = self.cloud_provider_group.checkedId()
        settings["cloud_folder"]       = self.cloud_folder_input.text()
        settings["gdrive_client_id"]     = self.gd_client_id_input.text()
        settings["gdrive_client_secret"] = self.gd_client_secret_input.text()
        settings["dropbox_app_key"]      = self.db_app_key_input.text()
        settings["dropbox_app_secret"]   = self.db_app_secret_input.text()
        # Local machine
        settings["lm_ip"]          = self.previous_paths.get("lm_ip", "")
        settings["lm_username"]    = self.lm_username_input.text()
        settings["lm_remote_path"] = self.lm_remote_path_input.text()
        settings["lm_port"]        = self.lm_port_input.text()
        settings["lm_ssh_key"]     = self.lm_ssh_key_input.text()

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
