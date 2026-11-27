from dataclasses import replace
from datetime import date

import pytest

from conftest import FakeImmich, FakeLillio, activity, page
from lillio_immich_sync import alerts as A
from lillio_immich_sync.lillio import Child, LillioError
from lillio_immich_sync.sync import META, SYNCED, SyncError, run_sync


def quiet(*_):
    pass


def journal():
    # newest first, two pages
    return {"1": [page(activity(3, "2026-03-03T10:00:00Z", title="Blocks", video=True),
                       activity(2, "2026-03-02T10:00:00Z", image=False)),
                  page(activity(1, "2026-03-01T10:00:00Z", description="Look what I'm doing today!"))]}


def test_first_run_uploads_everything_oldest_first(cfg, state, rec):
    lil, im = FakeLillio(journal()), FakeImmich()
    r = run_sync(cfg, lil, im, state, rec, log=quiet)
    assert [f for f, _ in im.uploads] == ["lillio_20260301_100000_1-1-image.jpg",
                                          "lillio_20260303_100000_1-3-image.jpg",
                                          "lillio_20260303_100000_1-3-video.mov"]
    assert r.uploaded == 3 and not r.failed and not rec.sent
    assert im.descriptions == {"asset-2": "Blocks", "asset-3": "Blocks"}  # boilerplate caption not written
    assert set(state.load(SYNCED)) == {"1:1:image", "1:3:image", "1:3:video"}
    assert state.load(META)["backfilled"] == ["1"]


def test_second_run_is_incremental_and_uploads_nothing(cfg, state, rec):
    run_sync(cfg, FakeLillio(journal()), FakeImmich(), state, rec, log=quiet)
    lil, im = FakeLillio(journal()), FakeImmich()
    r = run_sync(cfg, lil, im, state, rec, log=quiet)
    assert r.uploaded == 0 and im.uploads == []
    assert lil.requested == [("1", 1)]  # stopped at the first fully synced page


def test_new_items_on_top_are_picked_up_incrementally(cfg, state, rec):
    run_sync(cfg, FakeLillio(journal()), FakeImmich(), state, rec, log=quiet)
    j = journal()
    j["1"].insert(0, page(activity(4, "2026-03-04T10:00:00Z")))
    lil, im = FakeLillio(j), FakeImmich()
    run_sync(cfg, lil, im, state, rec, log=quiet)
    assert [f for f, _ in im.uploads] == ["lillio_20260304_100000_1-4-image.jpg"]
    assert lil.requested == [("1", 1), ("1", 2)]


def test_failure_isolated_alerted_once_and_retried(cfg, state, rec):
    bad = "https://media.test/big/1.jpg"
    r = run_sync(cfg, FakeLillio(journal(), broken_urls=[bad]), FakeImmich(), state, rec, log=quiet)
    assert r.uploaded == 2 and list(r.failed) == ["1:1:image"]
    assert [k for k, _ in rec.sent] == [A.ITEMS_FAILED]
    assert state.load(META)["backfilled"] == []  # keep full scans until the failure clears

    run_sync(cfg, FakeLillio(journal(), broken_urls=[bad]), FakeImmich(), state, rec, log=quiet)
    assert len(rec.sent) == 1  # same failure set: no repeat alert

    im = FakeImmich()
    run_sync(cfg, FakeLillio(journal()), im, state, rec, log=quiet)
    assert [f for f, _ in im.uploads] == ["lillio_20260301_100000_1-1-image.jpg"]
    assert state.load(META) == {"failed": [], "backfilled": ["1"]}


def test_dry_run_writes_nothing(cfg, state, rec):
    im = FakeImmich()
    r = run_sync(cfg, FakeLillio(journal()), im, state, rec, dry_run=True, log=quiet)
    assert r.todo == 3 and im.uploads == [] and state.load(SYNCED) == {} and state.load(META) == {}


def test_limit_does_not_mark_backfilled(cfg, state, rec):
    im = FakeImmich()
    run_sync(cfg, FakeLillio(journal()), im, state, rec, limit=1, log=quiet)
    assert len(im.uploads) == 1 and state.load(META)["backfilled"] == []
    im2 = FakeImmich()
    run_sync(cfg, FakeLillio(journal()), im2, state, rec, log=quiet)
    assert len(im2.uploads) == 2


def test_videos_can_be_disabled(cfg, state, rec):
    im = FakeImmich()
    run_sync(replace(cfg, sync_videos=False), FakeLillio(journal()), im, state, rec, log=quiet)
    assert all(f.endswith(".jpg") for f, _ in im.uploads) and len(im.uploads) == 2


def test_since_filters_and_stops_paging(cfg, state, rec):
    lil, im = FakeLillio(journal()), FakeImmich()
    run_sync(replace(cfg, sync_since=date(2026, 3, 3)), lil, im, state, rec, log=quiet)
    assert len(im.uploads) == 2 and ("1", 2) in lil.requested
    assert all("20260303" in f for f, _ in im.uploads)


def test_album_per_child_created_lazily_and_matched_trimmed(cfg, state, rec):
    pages = {"1": journal()["1"], "2": [page(activity(9, "2026-03-05T10:00:00Z"))]}
    lil = FakeLillio(pages, children=[Child("1", "Ada"), Child("2", "Bo")])
    im = FakeImmich(albums=[{"id": "existing", "albumName": "Daycare - Ada "}])
    run_sync(replace(cfg, immich_album="Daycare - {child}"), lil, im, state, rec, log=quiet)
    assert len(im.album_assets["existing"]) == 3
    assert im.created_albums == ["Daycare - Bo"]


def test_duplicate_upload_counts_and_skips_description(cfg, state, rec):
    im = FakeImmich(duplicate_names={"lillio_20260303_100000_1-3-image.jpg"})
    r = run_sync(cfg, FakeLillio(journal()), im, state, rec, log=quiet)
    assert r.duplicates == 1 and "asset-2" not in im.descriptions
    assert "1:3:image" in state.load(SYNCED)


def test_child_ids_override_discovery(cfg, state, rec):
    lil = FakeLillio({"7": [page(activity(1, "2026-01-01T00:00:00Z"))]}, children=[])
    r = run_sync(replace(cfg, lillio_child_ids=("7",)), lil, FakeImmich(), state, rec, log=quiet)
    assert r.children == ["7"] and r.uploaded == 1


def test_no_children_is_an_error(cfg, state, rec):
    with pytest.raises(SyncError):
        run_sync(cfg, FakeLillio({}, children=[]), FakeImmich(), state, rec, log=quiet)


def test_repeating_page_terminates(cfg, state, rec):
    same = page(activity(1, "2026-01-01T00:00:00Z"))

    class Looping(FakeLillio):
        def journal(self, child_id, n):
            self.requested.append((child_id, n))
            from lillio_immich_sync.lillio import parse_journal
            return parse_journal(same, child_id)

    lil = Looping({"1": []})
    r = run_sync(cfg, lil, FakeImmich(), state, rec, log=quiet)
    assert r.uploaded == 1 and len(lil.requested) == 2


def test_unknown_journal_shape_propagates(cfg, state, rec):
    with pytest.raises(LillioError):
        run_sync(cfg, FakeLillio({"1": [{"nope": 1}]}), FakeImmich(), state, rec, log=quiet)
