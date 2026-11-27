"""Minimal Immich v3 REST client (API key auth).

Permissions needed: asset.upload, asset.update, and, if IMMICH_ALBUM is set, album.read, album.create and
albumAsset.create.
"""
from __future__ import annotations

from datetime import datetime

import requests

MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "heic": "image/heic", "heif": "image/heif",
        "webp": "image/webp", "mp4": "video/mp4", "mov": "video/quicktime", "m4v": "video/x-m4v",
        "webm": "video/webm"}


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, session: requests.Session | None = None):
        self._base = base_url.rstrip("/") + "/api"
        self._s = session or requests.Session()
        self._s.headers["x-api-key"] = api_key

    def _req(self, method: str, path: str, timeout: int = 60, **kw):
        r = self._s.request(method, f"{self._base}{path}", timeout=timeout, **kw)
        r.raise_for_status()
        return r

    def albums(self) -> list[dict]:
        return self._req("GET", "/albums", 30).json()

    def find_album(self, name: str) -> dict | None:
        """Exact match after trimming whitespace (the web UI can leave trailing spaces in names)."""
        matches = [a for a in self.albums() if a["albumName"].strip() == name.strip()]
        if len(matches) > 1:
            raise LookupError(f"{len(matches)} Immich albums named {name!r}; rename one so the name is unique")
        return matches[0] if matches else None

    def create_album(self, name: str) -> dict:
        return self._req("POST", "/albums", json={"albumName": name.strip()}).json()

    def upload(self, data: bytes, filename: str, created: datetime) -> tuple[str, str]:
        """Upload one file. Returns (asset id, status) where status is 'created' or 'duplicate'."""
        ext = filename.rsplit(".", 1)[-1].lower()
        ts = created.isoformat()
        r = self._req("POST", "/assets", 600,
                      files={"assetData": (filename, data, MIME.get(ext, "application/octet-stream"))},
                      data={"fileCreatedAt": ts, "fileModifiedAt": ts, "filename": filename})
        body = r.json()
        return body["id"], body.get("status", "created")

    def set_description(self, asset_ids: list[str], description: str) -> None:
        self._req("PUT", "/assets", json={"ids": asset_ids, "description": description})

    def add_to_album(self, album_id: str, asset_ids: list[str]) -> list[dict]:
        """Returns per-asset results; error 'duplicate' means it was already in the album."""
        return self._req("PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids}).json()
