"""Lillio journal -> Immich sync engine (resumable, deduped, alert-aware)."""
from __future__ import annotations

from dataclasses import dataclass, field
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


def resolve_albums(cfg, immich, child: Child) -> dict[str, str | None]:
    """Albums this child's items go into: IMMICH_ALBUM then IMMICH_EXTRA_ALBUMS, deduped, in order.

    Value is the album id, or None if it does not exist yet (created on first upload). With
    IMMICH_CREATE_ALBUMS off, a missing album is an error, so a typo cannot create a stray album.
    """
    names: list[str] = []
    for template in (cfg.immich_album, *cfg.immich_extra_albums):
        name = album_name(template, child) if template else ""
        if name and name not in names:
            names.append(name)
    out: dict[str, str | None] = {}
    for name in names:
        album = immich.find_album(name)
        if album is None and not cfg.immich_create_albums:
            raise SyncError(f"Immich album {name!r} not found and IMMICH_CREATE_ALBUMS is off")
        out[name] = album["id"] if album else None
    return out


def run_sync(cfg, lillio, immich, state, alerter, *, dry_run: bool = False, limit: int = 0, full: bool = False,
             log: Callable = print) -> SyncResult:
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
    state.save(META, meta)
    return result
