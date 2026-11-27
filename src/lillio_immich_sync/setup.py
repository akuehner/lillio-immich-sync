"""Interactive `setup`: prompt for missing settings, save them to the env file (mode 600), then verify both logins."""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path
from typing import Callable

from .config import Config, ConfigError, load_env_file, parse_env_file

# (variable, prompt, secret)
PROMPTS = (
    ("LILLIO_BASE_URL", "Lillio portal address you sign in at", False),
    ("LILLIO_EMAIL", "Lillio email", False),
    ("LILLIO_PASSWORD", "Lillio password", True),
    ("IMMICH_URL", "Immich URL", False),
    ("IMMICH_API_KEY", "Immich API key", True),
)
DEFAULTS = {"LILLIO_BASE_URL": Config.lillio_base_url}


def _quote(value: str) -> str:
    # The env parser strips one pair of surrounding quotes, so quoting keeps spaces and symbols intact.
    return '"' + value + '"'


def set_values(text: str, values: dict[str, str]) -> str:
    """Set KEY=value lines in env-file text: replace an existing (or commented-out) line, else append."""
    lines = text.splitlines()
    for key, value in values.items():
        pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
        new = f"{key}={_quote(value)}"
        for i, line in enumerate(lines):
            if pattern.match(line) and not line.lstrip().startswith("#"):
                lines[i] = new
                break
        else:
            for i, line in enumerate(lines):
                if pattern.match(line):
                    lines[i] = new
                    break
            else:
                lines.append(new)
    return "\n".join(lines) + "\n"


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def verify(cfg: Config, out: Callable = print) -> bool:
    from .immich import ImmichClient
    from .lillio import LillioClient

    ok = True
    try:
        kids = LillioClient.from_config(cfg).children()
        out(f"Lillio: logged in; children: {', '.join(f'{c.name} ({c.id})' for c in kids) or 'none found'}")
        if not kids and not cfg.lillio_child_ids:
            out("  set LILLIO_CHILD_IDS to the number in the portal URL /accounts/<id>")
    except Exception as e:  # noqa: BLE001
        ok = False
        out(f"Lillio: FAILED ({type(e).__name__}: {e})")
    try:
        albums = ImmichClient(cfg.immich_url, cfg.immich_api_key).albums()
        out(f"Immich: API key works ({len(albums)} albums visible)")
    except Exception as e:  # noqa: BLE001
        ok = False
        hint = " (the key needs album.read)" if "403" in str(e) else ""
        out(f"Immich: FAILED ({type(e).__name__}: {e}){hint}")
    return ok


def run_setup(path: str | Path, *, ask: Callable = input, ask_secret: Callable = getpass.getpass,
              out: Callable = print, check: Callable = verify) -> int:
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    current = parse_env_file(text)
    out(f"Settings file: {path.resolve()}  (Enter keeps the value shown in brackets)")
    updates: dict[str, str] = {}
    for key, prompt, secret in PROMPTS:
        existing = current.get(key, "")
        if secret:
            value = ask_secret(f"{prompt}{' [keep saved]' if existing else ''}: ")
        else:
            shown = existing or DEFAULTS.get(key, "")
            value = ask(f"{prompt}{f' [{shown}]' if shown else ''}: ").strip() or shown
        if value and value != existing:
            updates[key] = value
    if updates:
        write_private(path, set_values(text, updates))
        out(f"Saved {', '.join(updates)} (file mode 600)")
    else:
        if path.exists():
            os.chmod(path, 0o600)
        out("No changes")
    try:
        cfg = Config.from_env(load_env_file(path, {}))
        cfg.require_lillio()
        cfg.require_immich()
    except ConfigError as e:
        out(f"configuration error: {e}")
        return 2
    out("Checking...")
    return 0 if check(cfg, out) else 1
