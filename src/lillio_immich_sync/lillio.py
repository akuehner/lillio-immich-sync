"""Lillio (formerly HiMama) parent-portal client: login, child discovery, and the photo journal feed.

There is no public API. This uses the same pages and JSON the parent web portal uses:
  GET  /login                                 CSRF token (Rails authenticity_token)
  POST /login                                 user[login], user[password]
  GET  /accounts, /headlines                  links of the form /accounts/<child id>
  GET  /accounts/<child id>/journal_api?page=N   {"intervals": [[{"activity": {...}}, ...], ...]}, newest first
See docs/lillio-api.md for the observed shapes and what is still unverified.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Iterator
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
DEFAULT_COOKIE_NAME = "_himama_session"
# Boilerplate caption the portal uses when the teacher writes nothing
BOILERPLATE = {"look what i'm doing today!", "look what i'm doing today"}
IMAGE_SIZE_PREFERENCE = ("original", "big", "large", "full", "medium")
# Menu links that also point at /accounts/<id> but are not the child's name
NAV_LABELS = {"profile", "edit profile", "journal", "entries", "crew", "manage crew", "reports", "milestones",
              "photos", "home", "headlines", "view", "view profile"}
_ACCOUNT_HREF =re.compile(r"^(?:https?://[^/]+)?/accounts/(\d+)/?$")


class LillioError(RuntimeError):
    """The portal answered in a way this client does not understand (layout or API change)."""


class AuthError(LillioError):
    """Login failed or the session is no longer valid."""


@dataclass(frozen=True)
class Child:
    id: str
    name: str


@dataclass(frozen=True)
class MediaItem:
    key: str  # stable identifier used for dedupe
    child_id: str
    kind: str  # "image" or "video"
    url: str
    created_at: datetime  # timezone-aware
    caption: str

    @property
    def extension(self) -> str:
        path = urlsplit(self.url).path
        ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if ext in ("jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "mp4", "mov", "m4v", "webm"):
            return "jpg" if ext == "jpeg" else ext
        return "jpg" if self.kind == "image" else "mp4"

    def filename(self) -> str:
        return f"lillio_{self.created_at.strftime('%Y%m%d_%H%M%S')}_{self.key.replace(':', '-')}.{self.extension}"


# --------------------------------------------------------------------------- parsing (pure, unit-tested)


def parse_timestamp(value: str, default_tz: tzinfo) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=default_tz)


def _text(value) -> str:
    if not isinstance(value, str):
        return ""
    if "<" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ")
    return " ".join(value.split())


def build_caption(title, description) -> str:
    parts: list[str] = []
    for p in (_text(title), _text(description)):
        if p and p.lower() not in BOILERPLATE and p not in parts:
            parts.append(p)
    return " - ".join(parts)


def _image_url(image) -> str:
    if isinstance(image, str):
        return image
    if not isinstance(image, dict):
        return ""
    for size in IMAGE_SIZE_PREFERENCE:
        v = image.get(size)
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
        if isinstance(v, str) and v:
            return v
    return image.get("url") or ""


def _video_url(video) -> str:
    if isinstance(video, str):
        return video
    return video.get("url") or "" if isinstance(video, dict) else ""


def iter_activities(data) -> Iterator[dict]:
    """Yield activity dicts from a journal_api page. Raises LillioError if the response shape is unknown."""
    if not isinstance(data, dict) or "intervals" not in data:
        raise LillioError("unexpected journal response (no 'intervals' key); the portal may have changed")
    intervals = data["intervals"] or []
    groups = intervals.values() if isinstance(intervals, dict) else intervals
    for group in groups:
        entries = group.values() if isinstance(group, dict) else group
        for entry in entries or []:
            if isinstance(entry, dict):
                act = entry.get("activity", entry)
                if isinstance(act, dict):
                    yield act


def media_from_activity(act: dict, child_id: str, default_tz: tzinfo) -> list[MediaItem]:
    """Photos and videos attached to one activity. Activities without media return []."""
    raw_ts = act.get("created_at") or act.get("date") or ""
    if not raw_ts:
        return []
    try:
        created = parse_timestamp(str(raw_ts), default_tz)
    except ValueError:
        return []
    caption = build_caption(act.get("title"), act.get("description"))
    base_id = act.get("activity_file_id") or act.get("id")
    out = []
    for kind, url in (("image", _image_url(act.get("image"))), ("video", _video_url(act.get("video")))):
        if not url:
            continue
        ident = str(base_id) if base_id is not None else hashlib.sha1(urlsplit(url).path.encode()).hexdigest()[:16]
        out.append(MediaItem(key=f"{child_id}:{ident}:{kind}", child_id=child_id, kind=kind, url=url,
                             created_at=created, caption=caption))
    return out


def parse_journal(data, child_id: str, default_tz: tzinfo = timezone.utc) -> tuple[int, list[MediaItem]]:
    """Return (number of activities on the page, media items on the page)."""
    count, items = 0, []
    for act in iter_activities(data):
        count += 1
        items += media_from_activity(act, child_id, default_tz)
    return count, items


def parse_children(html: str) -> list[Child]:
    soup = BeautifulSoup(html, "html.parser")
    names: dict[str, str] = {}  # child id -> first link text that is not a menu label (insertion order kept)
    for a in soup.find_all("a", href=True):
        m = _ACCOUNT_HREF.match(a["href"].strip())
        if m:
            text = " ".join(a.get_text(" ").split())
            if text.lower() in NAV_LABELS:
                text = ""
            if not names.get(m.group(1)):
                names[m.group(1)] = text
    return [Child(cid, name or cid) for cid, name in names.items()]


def login_form_present(html: str) -> bool:
    return BeautifulSoup(html, "html.parser").find("input", {"name": "user[password]"}) is not None


# --------------------------------------------------------------------------- HTTP client


class LillioClient:
    def __init__(self, base_url: str, *, default_tz: tzinfo = timezone.utc,
                 session: requests.Session | None = None, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.host = urlsplit(self.base).hostname or ""
        self.default_tz = default_tz
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self._media = requests.Session()  # no portal cookies on media hosts
        self._media.headers["User-Agent"] = USER_AGENT

    @classmethod
    def from_config(cls, cfg) -> "LillioClient":
        client = cls(cfg.lillio_base_url, default_tz=cfg.tz)
        if cfg.lillio_session_cookie:
            client.use_cookie(cfg.lillio_session_cookie)
        else:
            client.login(cfg.lillio_email, cfg.lillio_password)
        return client

    def use_cookie(self, cookie: str) -> None:
        """Accept 'name=value' or a bare value (assumed to be the classic _himama_session cookie)."""
        name, value = cookie.split("=", 1) if "=" in cookie else (DEFAULT_COOKIE_NAME, cookie)
        self.s.cookies.set(name.strip(), value.strip(), domain=self.host)

    def login(self, email: str, password: str) -> None:
        page = self.s.get(f"{self.base}/login", timeout=self.timeout)
        page.raise_for_status()
        token = BeautifulSoup(page.text, "html.parser").find("input", {"name": "authenticity_token"})
        if token is None or not token.get("value"):
            raise LillioError("no CSRF token on the login page; the portal may have changed")
        resp = self.s.post(f"{self.base}/login", timeout=self.timeout, allow_redirects=True, data={
            "authenticity_token": token["value"],
            "user[login]": email,
            "user[password]": password,
            "user[remember_me]": "1",
            "commit": "Log In",
        })
        if urlsplit(resp.url).path.rstrip("/") == "/login" or login_form_present(resp.text):
            raise AuthError("Lillio login failed; check LILLIO_EMAIL and LILLIO_PASSWORD")

    def _get(self, path: str, **kw) -> requests.Response:
        r = self.s.get(f"{self.base}{path}", timeout=self.timeout, **kw)
        if urlsplit(r.url).path.rstrip("/") == "/login":
            raise AuthError("Lillio session is not logged in (redirected to /login)")
        r.raise_for_status()
        return r

    def children(self) -> list[Child]:
        found: dict[str, Child] = {}
        for path in ("/accounts", "/headlines"):
            try:
                html = self._get(path).text
            except requests.HTTPError:
                continue
            for c in parse_children(html):
                found.setdefault(c.id, c)
        return list(found.values())

    def journal_page(self, child_id: str, page: int) -> dict:
        r = self._get(f"/accounts/{child_id}/journal_api", params={"page": page},
                      headers={"Accept": "application/json"})
        try:
            return r.json()
        except ValueError:
            if login_form_present(r.text):
                raise AuthError("Lillio session is not logged in") from None
            raise LillioError(f"journal_api for child {child_id} did not return JSON; the portal may have changed")

    def journal(self, child_id: str, page: int) -> tuple[int, list[MediaItem]]:
        return parse_journal(self.journal_page(child_id, page), child_id, self.default_tz)

    def download(self, url: str) -> bytes:
        if not url.startswith("http"):
            url = f"{self.base}/{url.lstrip('/')}"
        session = self.s if urlsplit(url).hostname == self.host else self._media
        r = session.get(url, timeout=300)
        r.raise_for_status()
        return r.content
