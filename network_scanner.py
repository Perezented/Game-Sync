import socket
import ipaddress
import concurrent.futures
import subprocess
import platform

from PyQt6.QtCore import QThread, pyqtSignal

# Suppress console-window flicker when running as a Windows EXE
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class NetworkScanner(QThread):
    """Scans the local /24 subnet for live hosts and guesses their OS."""

    scan_complete = pyqtSignal(list)  # list of (ip, os_type, label, mac)
    scan_status = pyqtSignal(str)  # progress messages

    # Ports used as host liveness + OS hints.
    PORT_SMB = 445
    PORT_RDP = 3389
    PORT_SSH = 22
    PROBE_PORTS = [PORT_SMB, PORT_RDP, PORT_SSH]

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
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return None

    def _probe_host(self, ip):
        open_ports = set()

        for port in self.PROBE_PORTS:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.4)
                    if s.connect_ex((ip, port)) == 0:
                        open_ports.add(port)
            except Exception:
                pass

        if not open_ports:
            return None

        os_type = self._guess_os_type(ip, open_ports)

        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            hostname = ip

        mac = self._get_mac_for_ip(ip)
        label = f"{ip}  ({hostname})  [{os_type}]"
        return (ip, os_type, label, mac)

    def _guess_os_type(self, ip, open_ports):
        # Strong Windows indicators.
        if self.PORT_SMB in open_ports or self.PORT_RDP in open_ports:
            return "Windows"

        if self.PORT_SSH in open_ports:
            banner = self._get_ssh_banner(ip)
            if "windows" in banner:
                return "Windows"
            if banner:
                return "Linux"

        return "Unknown"

    def _get_ssh_banner(self, ip):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.8)
                if s.connect_ex((ip, self.PORT_SSH)) != 0:
                    return ""
                data = s.recv(256)
                return data.decode("utf-8", errors="ignore").strip().lower()
        except Exception:
            return ""

    def _get_mac_for_ip(self, ip):
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["arp", "-a", ip],
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=_CREATE_NO_WINDOW,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if parts and parts[0] == ip and len(parts) >= 2:
                        return parts[1].replace("-", ":").lower()
            else:
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
