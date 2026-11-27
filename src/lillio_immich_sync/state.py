"""Small JSON state files in one directory. Writes are atomic; credentials files are mode 600."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class State:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def path(self, name: str) -> Path:
        return self.dir / name

    def load(self, name: str, default=None):
        p = self.path(name)
        if not p.exists():
            return {} if default is None else default
        return json.loads(p.read_text())

    def save(self, name: str, data, *, private: bool = False) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1)
            os.chmod(tmp, 0o600 if private else 0o644)
            os.replace(tmp, self.path(name))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
