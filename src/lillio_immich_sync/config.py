"""Configuration from environment variables (optionally seeded from an env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Missing or invalid configuration. The message names the variables, never their values."""


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Ignores blanks and # comments; strips one pair of surrounding quotes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def load_env_file(path: str | Path, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Merge an env file under the given environment (real environment variables win)."""
    merged = dict(parse_env_file(Path(path).read_text()))
    merged.update(os.environ if env is None else env)
    return merged


def _flag(value: str, default: bool) -> bool:
    if not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # Lillio
    lillio_base_url: str = "https://app.lillio.com"
    lillio_email: str = ""
    lillio_password: str = ""
    lillio_session_cookie: str = ""
    lillio_child_ids: tuple[str, ...] = ()
    # Local zone: for portal timestamps without an offset, and for the daemon schedule
    timezone: str = "UTC"
    # Immich
    immich_url: str = ""
    immich_api_key: str = ""
    immich_album: str = ""  # template; {child} is replaced by the child's name. Empty = no album
    immich_extra_albums: tuple[str, ...] = ()  # more album templates (IMMICH_EXTRA_ALBUMS, ';' separated)
    immich_create_albums: bool = True  # create missing albums; off = a missing album is an error
    # Behaviour
    sync_videos: bool = True
    sync_since: date | None = None
    # Schedule for the `daemon` command
    sync_times: tuple[time, ...] = (time(18, 0),)
    sync_days: frozenset[int] = frozenset(range(7))
    state_dir: Path = Path("state")
    # Alerts (all optional; both channels can be used together)
    alert_webhook_url: str = ""
    alert_webhook_template: str = '{"text": "$MESSAGE"}'
    alert_command: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env

        def s(name: str, default: str = "") -> str:
            return e.get(name, default).strip()

        since = None
        if s("SYNC_SINCE"):
            try:
                since = date.fromisoformat(s("SYNC_SINCE"))
            except ValueError:
                raise ConfigError("SYNC_SINCE must be a date like 2025-09-01") from None

        tz = s("TIMEZONE") or cls.timezone
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError("TIMEZONE must be an IANA zone name such as America/New_York") from None

        from .schedule import parse_days, parse_times

        defaults = cls()
        try:
            times = parse_times(s("SYNC_TIMES")) if s("SYNC_TIMES") else defaults.sync_times
        except ValueError:
            raise ConfigError("SYNC_TIMES must be HH:MM times, comma separated, e.g. 18:00 or 07:30,18:00") from None
        try:
            days = parse_days(s("SYNC_DAYS")) if s("SYNC_DAYS") else defaults.sync_days
        except ValueError:
            raise ConfigError("SYNC_DAYS must be daily, weekdays, weekends, a range like mon-fri, "
                              "or a list like mon,wed,fri") from None

        return cls(
            lillio_base_url=(s("LILLIO_BASE_URL") or defaults.lillio_base_url).rstrip("/"),
            lillio_email=s("LILLIO_EMAIL"),
            lillio_password=e.get("LILLIO_PASSWORD", ""),
            lillio_session_cookie=s("LILLIO_SESSION_COOKIE"),
            lillio_child_ids=tuple(x.strip() for x in s("LILLIO_CHILD_IDS").split(",") if x.strip()),
            timezone=tz,
            immich_url=s("IMMICH_URL").rstrip("/"),
            immich_api_key=s("IMMICH_API_KEY"),
            immich_album=s("IMMICH_ALBUM"),
            immich_extra_albums=tuple(x.strip() for x in s("IMMICH_EXTRA_ALBUMS").split(";") if x.strip()),
            immich_create_albums=_flag(s("IMMICH_CREATE_ALBUMS"), True),
            sync_videos=_flag(s("SYNC_VIDEOS"), True),
            sync_since=since,
            sync_times=times,
            sync_days=days,
            state_dir=Path(s("STATE_DIR") or defaults.state_dir),
            alert_webhook_url=s("ALERT_WEBHOOK_URL"),
            alert_webhook_template=s("ALERT_WEBHOOK_TEMPLATE") or defaults.alert_webhook_template,
            alert_command=s("ALERT_COMMAND"),
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _require(self, pairs: dict[str, str]) -> None:
        missing = [name for name, value in pairs.items() if not value]
        if missing:
            raise ConfigError("missing required settings: " + ", ".join(missing))

    def require_immich(self) -> None:
        self._require({"IMMICH_URL": self.immich_url, "IMMICH_API_KEY": self.immich_api_key})

    def require_lillio(self) -> None:
        if self.lillio_session_cookie:
            return
        if not (self.lillio_email and self.lillio_password):
            raise ConfigError("set LILLIO_EMAIL and LILLIO_PASSWORD, or LILLIO_SESSION_COOKIE")

    def secrets(self) -> list[str]:
        """Values that must never appear in logs or alerts."""
        return [v for v in (self.immich_api_key, self.lillio_password, self.lillio_session_cookie,
                            self.alert_webhook_url) if v]
