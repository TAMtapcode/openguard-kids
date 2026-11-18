import csv
from pathlib import Path


class CategoryDB:
    def __init__(self, path: Path):
        self.map: dict[str, str] = {}
        self.doh: list[str] = []
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                d = row["domain"].strip().lower()
                self.map[d] = row["category"].strip()
                if row.get("flag", "").strip() == "doh":
                    self.doh.append(d)
