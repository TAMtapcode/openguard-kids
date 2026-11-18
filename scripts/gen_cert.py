"""Tạo chứng chỉ TLS tự ký (môi trường phòng lab) và in mã ghim SHA-256 để Agent ghim chứng chỉ.
   python scripts/gen_cert.py --host ogk.lan --ip 192.168.1.10 --out certs"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.tls import make_cert  # noqa: E402,F401  (giữ tên cũ cho test và script)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", action="append", default=[])
    ap.add_argument("--ip", action="append", default=["127.0.0.1"])
    ap.add_argument("--out", default="certs")
    a = ap.parse_args()
    fp = make_cert(a.host or ["localhost"], a.ip, Path(a.out), days=825)
    print(f"Đã tạo {a.out}/server.pem và server.key\nMã ghim (dùng cho: python -m agent enroll --pin ...):\n{fp}")
