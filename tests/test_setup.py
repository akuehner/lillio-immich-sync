import stat

from lillio_immich_sync.config import parse_env_file
from lillio_immich_sync.setup import run_setup, set_values


def test_set_values_replaces_active_then_commented_then_appends():
    text = "# head\nA=\n# B=old\n# C=x\nC=y\n"
    out = set_values(text, {"A": "1", "B": "2", "C": "3", "D": "4"})
    assert out == "# head\nA='1'\nB='2'\n# C=x\nC='3'\nD='4'\n"


def test_quoting_round_trips_awkward_passwords():
    for pw in [' lead', 'has "quotes"', "#hash=eq", "'single'"]:
        assert parse_env_file(set_values("", {"P": pw}))["P"] == pw


def answers(*vals):
    it = iter(vals)
    return lambda prompt: next(it)


def test_setup_prompts_saves_private_and_verifies(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LILLIO_BASE_URL=https://portal.test\nLILLIO_EMAIL=\nIMMICH_URL=http://immich.test\n"
                   "IMMICH_API_KEY=\nIMMICH_ALBUM=Daycare - {child}\n")
    seen = []
    rc = run_setup(env, ask=answers("", "me@example.test", ""), ask_secret=answers("s3cret pw", "KEY"),
                   out=lambda *_: None, check=lambda cfg, out: seen.append(cfg) or True)
    assert rc == 0
    vals = parse_env_file(env.read_text())
    assert vals["LILLIO_EMAIL"] == "me@example.test" and vals["LILLIO_PASSWORD"] == "s3cret pw"
    assert vals["IMMICH_API_KEY"] == "KEY" and vals["IMMICH_ALBUM"] == "Daycare - {child}"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert seen[0].lillio_base_url == "https://portal.test" and seen[0].immich_api_key == "KEY"


def test_setup_enter_keeps_saved_secrets(tmp_path):
    env = tmp_path / ".env"
    env.write_text('LILLIO_BASE_URL=https://p.test\nLILLIO_EMAIL=a@b\nLILLIO_PASSWORD="pw"\nIMMICH_URL=http://i\nIMMICH_API_KEY=K\n')
    before = env.read_text()
    rc = run_setup(env, ask=answers("", "", ""), ask_secret=answers("", ""), out=lambda *_: None,
                   check=lambda cfg, out: cfg.lillio_password == "pw" and cfg.immich_api_key == "K")
    assert rc == 0 and env.read_text() == before


def test_setup_creates_missing_file_and_reports_failed_check(tmp_path):
    env = tmp_path / "new.env"
    rc = run_setup(env, ask=answers("", "e", "http://i"), ask_secret=answers("p", "k"), out=lambda *_: None,
                   check=lambda cfg, out: False)
    assert rc == 1 and env.exists()
    assert parse_env_file(env.read_text())["LILLIO_BASE_URL"] == "https://app.lillio.com"


def test_dollar_values_single_quoted_for_compose():
    assert set_values("", {"T": '{"content": "$MESSAGE"}'}) == "T='{\"content\": \"$MESSAGE\"}'\n"
    assert set_values("", {"P": "it's"}) == 'P="it\'s"\n'
