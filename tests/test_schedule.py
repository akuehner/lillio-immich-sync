import argparse
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from lillio_immich_sync import cli
from lillio_immich_sync.config import Config, ConfigError
from lillio_immich_sync.schedule import next_run, parse_days, parse_times

NY = ZoneInfo("America/New_York")
ALL = frozenset(range(7))


def test_parse_times_sorted_and_deduped():
    assert parse_times("18:00, 07:30,18:00") == (time(7, 30), time(18, 0))


@pytest.mark.parametrize("text,days", [("daily", set(range(7))), ("weekdays", {0, 1, 2, 3, 4}),
                                       ("mon-fri", {0, 1, 2, 3, 4}), ("fri-mon", {4, 5, 6, 0}),
                                       ("Mon,wednesday", {0, 2}), ("weekends", {5, 6})])
def test_parse_days(text, days):
    assert parse_days(text) == days


@pytest.mark.parametrize("bad", ["", "funday", "x-y"])
def test_parse_days_bad(bad):
    with pytest.raises(ValueError):
        parse_days(bad)


def test_next_run_later_today_then_tomorrow():
    t = (time(18, 0),)
    assert next_run(datetime(2026, 3, 2, 9, 0, tzinfo=NY), t, ALL) == datetime(2026, 3, 2, 18, 0, tzinfo=NY)
    assert next_run(datetime(2026, 3, 2, 18, 0, tzinfo=NY), t, ALL) == datetime(2026, 3, 3, 18, 0, tzinfo=NY)


def test_next_run_skips_to_monday_on_weekdays():
    fri_evening = datetime(2026, 3, 6, 19, 0, tzinfo=NY)  # Friday
    assert next_run(fri_evening, (time(18, 0),), parse_days("weekdays")) == datetime(2026, 3, 9, 18, 0, tzinfo=NY)


def test_next_run_across_dst_keeps_wall_clock():
    before = datetime(2026, 3, 7, 19, 0, tzinfo=NY)  # DST starts 2026-03-08
    assert next_run(before, (time(18, 0),), ALL).hour == 18


def test_config_schedule_defaults_and_errors():
    c = Config.from_env({})
    assert c.sync_times == (time(18, 0),) and c.sync_days == ALL and c.timezone == "UTC"
    c = Config.from_env({"SYNC_TIMES": "06:00,20:00", "SYNC_DAYS": "mon-fri", "TIMEZONE": "America/New_York"})
    assert c.sync_times == (time(6), time(20)) and c.sync_days == {0, 1, 2, 3, 4}
    for env in ({"SYNC_TIMES": "6pm"}, {"SYNC_DAYS": "someday"}):
        with pytest.raises(ConfigError):
            Config.from_env(env)


def test_daemon_sleeps_until_due_and_runs(monkeypatch, tmp_path):
    cfg = Config(lillio_email="e", lillio_password="p", immich_url="http://i", immich_api_key="k",
                 timezone="America/New_York", state_dir=tmp_path)
    clock = [datetime(2026, 3, 2, 17, 0, tzinfo=NY)]
    runs = []

    def fake_sleep(seconds):
        clock[0] += timedelta(seconds=seconds)

    def fake_sync(cfg, args, alerter, state):
        runs.append(clock[0])
        if len(runs) == 1:
            raise RuntimeError("portal down")  # must alert and keep going
        return 0

    monkeypatch.setattr(cli, "cmd_sync", fake_sync)
    sent = []

    class Rec:
        def send(self, kind, msg):
            sent.append(kind)

    args = argparse.Namespace(run_now=False, dry_run=False, limit=0, full=False)
    cli.cmd_daemon(cfg, args, Rec(), None, now=lambda: clock[0], sleep=fake_sleep, max_runs=2)
    assert runs == [datetime(2026, 3, 2, 18, 0, tzinfo=NY), datetime(2026, 3, 3, 18, 0, tzinfo=NY)]
    assert sent == ["run_failed"]
