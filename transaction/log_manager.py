import json
from pathlib import Path
from typing import Dict, Any

class LogManager:
    def __init__(self, path: str):
        self.path = Path(path)
        self.file = None

    def _open(self):
        if self.file is None or self.file.closed:
            self.file = open(self.path, "a", encoding="utf-8")

    def write(self, record: Dict[str, Any]):
        self._open()
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()          # ensure it's written to disk

    def flush(self):
        if self.file and not self.file.closed:
            self.file.flush()

    def close(self):
        if self.file and not self.file.closed:
            self.file.close()