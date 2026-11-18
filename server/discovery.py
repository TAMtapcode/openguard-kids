"""Máy chủ tự xưng danh trong mạng LAN (UDP 48600) để máy con khỏi phải nhập địa chỉ IP.
Chỉ trả tên máy và địa chỉ; không có bí mật nào đi qua kênh này (chứng chỉ được xác thực bằng mã cài đặt)."""
import json
import socket
import threading

DISCOVERY_PORT = 48600
MAGIC = b"OGK-DISCOVER/1"


def _ip_towards(addr: str) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((addr, 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class DiscoveryResponder(threading.Thread):
    def __init__(self, https_port: int, scheme="https", port=DISCOVERY_PORT, bind=""):
        super().__init__(name="ogk-discovery", daemon=True)
        self.https_port, self.scheme, self._halt = https_port, scheme, threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.5)

    def run(self):
        while not self._halt.is_set():
            try:
                data, addr = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.startswith(MAGIC):
                msg = {"app": "ogk", "name": socket.gethostname(), "url": f"{self.scheme}://{_ip_towards(addr[0])}:{self.https_port}"}
                try:
                    self.sock.sendto(json.dumps(msg).encode(), addr)
                except OSError:
                    pass

    def stop(self):
        self._halt.set()
        self.sock.close()
