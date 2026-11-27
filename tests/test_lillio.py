from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import requests

from conftest import activity, page
from lillio_immich_sync.lillio import (AuthError, LillioClient, LillioError, build_caption, parse_children,
                                       parse_journal)


def test_parse_journal_prefers_big_image_and_keeps_video():
    data = page(activity(1, "2026-03-02T10:15:00.000-05:00", title="Painting", video=True))
    count, items = parse_journal(data, "42")
    assert count == 1
    assert [(i.kind, i.url) for i in items] == [("image", "https://media.test/big/1.jpg"),
                                                ("video", "https://media.test/vid/1.mov")]
    assert items[0].key == "42:1:image" and items[1].key == "42:1:video"
    assert items[0].created_at.utcoffset() == timedelta(hours=-5)
    assert items[0].caption == "Painting"


def test_parse_journal_skips_activities_without_media_but_counts_them():
    data = page(activity(1, "2026-03-02T10:15:00Z", image=False), activity(2, "2026-03-02T11:00:00Z"))
    count, items = parse_journal(data, "42")
    assert count == 2 and [i.key for i in items] == ["42:2:image"]


def test_parse_journal_accepts_dict_shaped_intervals():
    data = {"intervals": {"2026-03-02": [activity(7, "2026-03-02T10:00:00Z")]}}
    assert [i.key for i in parse_journal(data, "1")[1]] == ["1:7:image"]


def test_parse_journal_naive_timestamp_uses_default_zone():
    tz = ZoneInfo("America/New_York")
    _, items = parse_journal(page(activity(1, "2026-07-01T09:00:00")), "1", tz)
    assert items[0].created_at == datetime(2026, 7, 1, 9, 0, tzinfo=tz)


def test_parse_journal_falls_back_to_url_hash_without_ids():
    data = page({"activity": {"created_at": "2026-01-01T00:00:00Z", "image": "https://m.test/a/b.png"}})
    item = parse_journal(data, "1")[1][0]
    assert item.key.startswith("1:") and item.extension == "png"


def test_parse_journal_unknown_shape_raises():
    with pytest.raises(LillioError):
        parse_journal({"activities": []}, "1")


def test_caption_drops_boilerplate_html_and_repeats():
    assert build_caption("Look what I'm doing today!", "<p>Sand  <b>table</b></p>") == "Sand table"
    assert build_caption("Snack", "Snack") == "Snack"
    assert build_caption(None, None) == ""


def test_filename_is_stable_and_safe():
    item = parse_journal(page(activity(5, "2026-03-02T10:15:09Z")), "42")[1][0]
    assert item.filename() == "lillio_20260302_101509_42-5-image.jpg"


def test_parse_children_from_links():
    html = """<a href="/accounts/111"><img></a><a href="/accounts/111"> Ada  Smith </a>
              <a href="https://portal.test/accounts/222/">Bo</a><a href="/accounts/333/reports">x</a>"""
    assert [(c.id, c.name) for c in parse_children(html)] == [("111", "Ada Smith"), ("222", "Bo")]


def test_parse_children_skips_menu_labels():
    html = """<a href="/accounts/5">Profile</a><a href="/accounts/5/journal">Journal</a><a href="/accounts/5"></a>
              <a href="/accounts/5">Ada Smith</a><a href="/accounts/6">Profile</a>"""
    assert [(c.id, c.name) for c in parse_children(html)] == [("5", "Ada Smith"), ("6", "6")]


class FakeResp:
    def __init__(self, url, text="", status=200, payload=None):
        self.url, self.text, self.status_code, self._payload = url, text, status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = responses  # list consumed in order
        self.headers, self.calls = {}, []
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.responses.pop(0)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.responses.pop(0)


LOGIN_HTML = '<form><input name="authenticity_token" value="tok"><input name="user[password]"></form>'


def test_login_posts_csrf_and_credentials():
    s = FakeSession([FakeResp("https://p.test/login", LOGIN_HTML), FakeResp("https://p.test/headlines", "<p>hi</p>")])
    LillioClient("https://p.test", session=s).login("e@x", "pw")
    method, url, kw = s.calls[1]
    assert (method, url) == ("POST", "https://p.test/login")
    assert kw["data"]["authenticity_token"] == "tok" and kw["data"]["user[login]"] == "e@x"


def test_login_failure_detected():
    s = FakeSession([FakeResp("https://p.test/login", LOGIN_HTML), FakeResp("https://p.test/login", LOGIN_HTML)])
    with pytest.raises(AuthError):
        LillioClient("https://p.test", session=s).login("e@x", "bad")


def test_journal_redirect_to_login_is_auth_error():
    s = FakeSession([FakeResp("https://p.test/login", LOGIN_HTML)])
    with pytest.raises(AuthError):
        LillioClient("https://p.test", session=s).journal_page("1", 1)


def test_journal_non_json_is_lillio_error():
    s = FakeSession([FakeResp("https://p.test/accounts/1/journal_api", "<html>new layout</html>")])
    with pytest.raises(LillioError):
        LillioClient("https://p.test", session=s).journal_page("1", 1)


def test_cookie_bare_value_and_named():
    c = LillioClient("https://p.test", session=FakeSession([]))
    c.use_cookie("abc")
    c.use_cookie("_other=def")
    assert c.s.cookies.get("_himama_session") == "abc" and c.s.cookies.get("_other") == "def"
