from datetime import timezone

import pytest

from lillio_immich_sync.config import Config
from lillio_immich_sync.lillio import Child, parse_journal
from lillio_immich_sync.state import State


class Recorder:
    """Alerter stand-in capturing (kind, message)."""

    def __init__(self):
        self.sent = []

    def send(self, kind, message):
        self.sent.append((kind, message))


def activity(aid, ts, *, image=True, video=False, title="", description=""):
    act = {"activity_file_id": aid, "id": aid * 10, "created_at": ts, "title": title, "description": description,
           "image": None, "video": None}
    if image:
        act["image"] = {"url": f"https://media.test/img/{aid}.jpg", "big": {"url": f"https://media.test/big/{aid}.jpg"}}
    if video:
        act["video"] = {"url": f"https://media.test/vid/{aid}.mov"}
    return {"activity": act}


def page(*entries):
    return {"intervals": [list(entries)]}


class FakeLillio:
    """Journal pages per child; page numbers start at 1. Missing pages are empty."""

    def __init__(self, pages: dict[str, list[dict]], children=None, broken_urls=()):
        self.pages = pages
        self._children = children if children is not None else [Child(cid, f"Kid{cid}") for cid in pages]
        self.broken = set(broken_urls)
        self.requested = []
        self.downloads = []

    def children(self):
        return self._children

    def journal(self, child_id, n):
        self.requested.append((child_id, n))
        pages = self.pages.get(child_id, [])
        data = pages[n - 1] if n <= len(pages) else {"intervals": []}
        return parse_journal(data, child_id, timezone.utc)

    def download(self, url):
        self.downloads.append(url)
        if url in self.broken:
            raise IOError("boom")
        return b"bytes:" + url.encode()


class FakeImmich:
    def __init__(self, albums=None, duplicate_names=()):
        self._albums = list(albums or [])
        self.uploads = []
        self.descriptions = {}
        self.album_assets = {}
        self.created_albums = []
        self.duplicate_names = set(duplicate_names)

    def albums(self):
        return self._albums

    def get_album(self, album_id):
        for a in self._albums:
            if a["id"] == album_id:
                return a
        raise KeyError(album_id)

    def find_album(self, name):
        for a in self._albums:
            if a["albumName"].strip() == name.strip():
                return a
        return None

    def create_album(self, name):
        a = {"id": f"alb-{len(self._albums)}", "albumName": name}
        self._albums.append(a)
        self.created_albums.append(name)
        return a

    def upload(self, data, filename, created):
        self.uploads.append((filename, created))
        status = "duplicate" if filename in self.duplicate_names else "created"
        return f"asset-{len(self.uploads)}", status

    def set_description(self, ids, description):
        for i in ids:
            self.descriptions[i] = description

    def add_to_album(self, album_id, ids):
        self.album_assets.setdefault(album_id, []).extend(ids)
        return [{"id": i, "success": True} for i in ids]


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "state")


@pytest.fixture
def cfg(tmp_path):
    return Config(lillio_email="u@example.test", lillio_password="pw", immich_url="http://immich.test",
                  immich_api_key="K", state_dir=tmp_path / "state")
