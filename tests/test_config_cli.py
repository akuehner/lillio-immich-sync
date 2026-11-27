from datetime import date

import pytest

from lillio_immich_sync import cli
from lillio_immich_sync.alerts import Alerter, render_template
from lillio_immich_sync.config import Config, ConfigError, parse_env_file


def test_from_env_parses_everything():
    c = Config.from_env({"LILLIO_BASE_URL": "https://portal.test/", "LILLIO_CHILD_IDS": " 1, 2 ,",
                         "LILLIO_PASSWORD": " spaced ", "SYNC_VIDEOS": "no", "SYNC_SINCE": "2025-09-01",
                         "TIMEZONE": "America/New_York", "IMMICH_ALBUM": "Daycare - {child}"})
    assert c.lillio_base_url == "https://portal.test"
    assert c.lillio_child_ids == ("1", "2")
    assert c.lillio_password == " spaced "  # passwords are not stripped
    assert c.sync_videos is False and c.sync_since == date(2025, 9, 1)


def test_defaults():
    c = Config.from_env({})
    assert c.lillio_base_url == "https://app.lillio.com" and c.sync_videos is True and c.sync_since is None


@pytest.mark.parametrize("env", [{"SYNC_SINCE": "yesterday"}, {"TIMEZONE": "Mars/Base"}])
def test_bad_values(env):
    with pytest.raises(ConfigError):
        Config.from_env(env)


def test_require_lillio_accepts_cookie_or_credentials():
    Config(lillio_session_cookie="x").require_lillio()
    Config(lillio_email="a", lillio_password="b").require_lillio()
    with pytest.raises(ConfigError):
        Config(lillio_email="a").require_lillio()


def test_env_file_quotes_and_precedence(tmp_path):
    assert parse_env_file('# c\nA="1"\nB=\'2\'\nbad\n') == {"A": "1", "B": "2"}


def test_secrets_scrubbed():
    c = Config(lillio_password="hunter2", immich_api_key="KEY")
    assert cli.scrub("bad hunter2 KEY", c.secrets()) == "bad <secret> <secret>"


def test_shape_redacts_content():
    out = cli.shape({"title": "Ada at lunch", "image": {"url": "https://s3.test/x/y.jpg?sig=SECRET"}, "n": [1, 2]})
    assert out["title"] == "<str>"
    assert "SECRET" not in str(out) and out["image"]["url"].startswith("<url s3.test")
    assert out["n"] == ["<int>", "... 2 items"]


def test_template_escaping_and_webhook():
    assert render_template('{"content": "$MESSAGE"}', "k", 'a "b"\n') == '{"content": "a \\"b\\"\\n"}'
    posted = []
    Alerter("https://hook.test", post=lambda url, **kw: posted.append(kw["data"])).send("k", "hi")
    assert posted == ['{"text": "hi"}']


def test_missing_config_exit_code(capsys, monkeypatch):
    for k in ("LILLIO_EMAIL", "LILLIO_PASSWORD", "LILLIO_SESSION_COOKIE", "IMMICH_URL", "IMMICH_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["sync"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_extra_albums_config():
    c = Config.from_env({"IMMICH_EXTRA_ALBUMS": "Family; Kids, 2026 ;;", "IMMICH_CREATE_ALBUMS": "0"})
    assert c.immich_extra_albums == ("Family", "Kids, 2026") and c.immich_create_albums is False
    assert Config.from_env({}).immich_create_albums is True
