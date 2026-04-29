import socket
import ipaddress
import concurrent.futures
import subprocess
import platform

from PyQt6.QtCore import QThread, pyqtSignal


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
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return None

    def _probe_host(self, ip):
        os_type = "Unknown"
        alive = False

        for port, hint in self.OS_PORTS:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.4)
                    if s.connect_ex((ip, port)) == 0:
                        alive = True
                        os_type = hint
                        break
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
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["arp", "-a", ip],
                    capture_output=True,
                    text=True,
                    check=False,
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
