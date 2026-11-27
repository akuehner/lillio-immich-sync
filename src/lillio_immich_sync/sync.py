"""Lillio journal -> Immich sync engine (resumable, deduped, alert-aware)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from . import alerts as A
from .lillio import Child, LillioError, MediaItem

SYNCED = "synced.json"  # media key -> Immich asset id
META = "sync-meta.json"  # {"backfilled": [child ids], "failed": [media keys]}
MAX_PAGES = 5000  # safety stop in case the portal ignores ?page=


class SyncError(RuntimeError):
    pass


@dataclass
class SyncResult:
    children: list[str] = field(default_factory=list)
    found: int = 0
    todo: int = 0
    uploaded: int = 0
    duplicates: int = 0
    failed: dict = field(default_factory=dict)
    dry_run: bool = False


def resolve_children(cfg, lillio) -> list[Child]:
    discovered = lillio.children()
    if cfg.lillio_child_ids:
        by_id = {c.id: c for c in discovered}
        return [by_id.get(cid, Child(cid, cid)) for cid in cfg.lillio_child_ids]
    if not discovered:
        raise SyncError("no children found on the portal; set LILLIO_CHILD_IDS (see the `children` command)")
    return discovered


def collect(lillio, child_id: str, *, synced: dict, incremental: bool, since=None,
            log: Callable = print) -> list[MediaItem]:
    """Page through the journal (newest first) and return its media, deduped by key.

    incremental: stop at the first page whose media are all already synced.
    since: stop once a whole page is older than this date.
    """
    items: dict[str, MediaItem] = {}
    for page in range(1, MAX_PAGES + 1):
        count, page_items = lillio.journal(child_id, page)
        if count == 0:
            return list(items.values())
        new = [i for i in page_items if i.key not in items]
        if page_items and not new:
            log(f"child {child_id}: page {page} repeats earlier items; assuming end of journal")
            return list(items.values())
        items.update((i.key, i) for i in new)
        if incremental and page_items and all(i.key in synced for i in page_items):
            return list(items.values())
        if since and page_items and all(i.created_at.date() < since for i in page_items):
            return list(items.values())
    raise LillioError(f"journal for child {child_id} has more than {MAX_PAGES} pages; pagination may be broken")


def album_name(template: str, child: Child) -> str:
    return template.replace("{child}", child.name).strip()


ALBUM_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def resolve_albums(cfg, immich, child: Child) -> dict[str, str | None]:
    """Albums this child's items go into: IMMICH_ALBUM then IMMICH_EXTRA_ALBUMS, deduped, in order.

    Each entry is an album name (with optional {child}), an album id, or an album URL from the web UI.
    Returns album name -> id, where id is None if the album does not exist yet (created on first upload).
    Ids and URLs must point at an existing album. With IMMICH_CREATE_ALBUMS off, a missing named album is
    an error, so a typo cannot create a stray album.
    """
    out: dict[str, str | None] = {}
    for template in (cfg.immich_album, *cfg.immich_extra_albums):
        if not template:
            continue
        m = ALBUM_ID.search(template)
        if m:
            try:
                album = immich.get_album(m.group(0).lower())
            except Exception as e:  # noqa: BLE001
                raise SyncError(f"Immich album id {m.group(0)} not found or not accessible ({type(e).__name__})") from None
            name, album_id = album["albumName"].strip(), album["id"]
        else:
            name = album_name(template, child)
            if not name:
                continue
            album = immich.find_album(name)
            if album is None and not cfg.immich_create_albums:
                raise SyncError(f"Immich album {name!r} not found and IMMICH_CREATE_ALBUMS is off")
            album_id = album["id"] if album else None
        if album_id is not None and album_id in out.values():
            continue
        out.setdefault(name, album_id)
    return out


def run_sync(cfg, lillio, immich, state, alerter, *, dry_run: bool = False, limit: int = 0, full: bool = False,
             log: Callable = print, now: datetime | None = None) -> SyncResult:
    result = SyncResult(dry_run=dry_run)
    synced: dict = state.load(SYNCED)
    meta: dict = state.load(META)
    backfilled = set(meta.get("backfilled", []))
    pending_failures = bool(meta.get("failed"))
    budget = limit or None

    for child in resolve_children(cfg, lillio):
        result.children.append(child.name)
        incremental = not full and not pending_failures and child.id in backfilled
        items = collect(lillio, child.id, synced=synced, incremental=incremental, since=cfg.sync_since, log=log)
        if not items and not incremental:
            log(f"child {child.name!r}: the journal has no photos or videos")
        result.found += len(items)
        if items and not dry_run:
            newest = max(i.created_at for i in items).isoformat()
            known = meta.setdefault("newest", {}).get(child.id)
            if not known or datetime.fromisoformat(newest) > datetime.fromisoformat(known):
                meta["newest"][child.id] = newest
        todo = sorted((i for i in items
                       if i.key not in synced
                       and (cfg.sync_videos or i.kind == "image")
                       and (cfg.sync_since is None or i.created_at.date() >= cfg.sync_since)),
                      key=lambda i: i.created_at)  # oldest first, so an interrupted run leaves no gaps
        result.todo += len(todo)
        truncated = False
        if budget is not None:
            truncated = len(todo) > budget
            todo, budget = todo[:budget], max(0, budget - len(todo))

        albums = resolve_albums(cfg, immich, child)  # name -> id (None = create on first upload)
        log(f"child {child.name!r}: {len(items)} items in journal, {len(todo)} to upload"
            + "".join(f", album {n!r} ({'exists' if i else 'will be created'})" for n, i in albums.items())
            + (" (incremental)" if incremental else " (full scan)"))
        if dry_run:
            for i in todo[:20]:
                log(f"  would upload {i.filename()}  {i.caption[:60]!r}")
            continue

        failures_before = len(result.failed)
        for item in todo:  # one bad file must not block the rest
            try:
                data = lillio.download(item.url)
                asset_id, status = immich.upload(data, item.filename(), item.created_at)
                if status == "duplicate":
                    result.duplicates += 1
                elif item.caption:
                    immich.set_description([asset_id], item.caption)
                for name, album_id in albums.items():
                    if album_id is None:
                        album_id = albums[name] = immich.create_album(name)["id"]
                    immich.add_to_album(album_id, [asset_id])
            except Exception as e:  # noqa: BLE001
                result.failed[item.key] = f"{item.filename()}: {type(e).__name__}: {str(e)[:120]}"
                continue
            synced[item.key] = asset_id
            state.save(SYNCED, synced)  # per item, so a crash never re-uploads
            result.uploaded += 1
        # A completed scan with nothing left over lets later runs stop at the first already-synced page.
        if len(result.failed) == failures_before and not truncated:
            backfilled.add(child.id)

    log(f"uploaded {result.uploaded} ({result.duplicates} already in Immich), failed {len(result.failed)}")
    if dry_run:
        return result
    if result.failed and sorted(result.failed) != meta.get("failed", []):  # once per distinct failure set
        alerter.send(A.ITEMS_FAILED, "lillio-immich-sync: %d item(s) failed:\n%s" % (
            len(result.failed), "\n".join(sorted(result.failed.values())[:15])))
    meta["failed"] = sorted(result.failed)
    meta["backfilled"] = sorted(backfilled)
    _stale_check(cfg, alerter, meta, result, now or datetime.now(timezone.utc))
    state.save(META, meta)
    return result


def _stale_check(cfg, alerter, meta: dict, result: SyncResult, now: datetime) -> None:
    """Alert once when a child's newest journal item is older than STALE_ALERT_DAYS; re-arm when fresh again.

    A portal change that makes the journal look empty (rather than erroring) shows up here.
    """
    if not cfg.stale_alert_days:
        return
    alerted = set(meta.get("stale_alerted", []))
    for child_id, newest in meta.get("newest", {}).items():
        age_days = (now - datetime.fromisoformat(newest)).total_seconds() / 86400
        if age_days > cfg.stale_alert_days:
            if child_id not in alerted:
                alerter.send(A.JOURNAL_STALE, f"lillio-immich-sync: no new journal items for child {child_id} in "
                             f"{age_days:.0f} days (newest {newest[:10]}). Check the portal and `probe`.")
                alerted.add(child_id)
        else:
            alerted.discard(child_id)
    meta["stale_alerted"] = sorted(alerted)
