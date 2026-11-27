"""Command line interface: sync | daemon | children | probe | list-albums."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from urllib.parse import urlsplit

from . import __version__
from . import alerts as A
from .config import Config, ConfigError, load_env_file
from .immich import ImmichClient
from .lillio import LillioClient
from .state import State


def scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "<secret>")
    return text


def _lillio(cfg: Config) -> LillioClient:
    cfg.require_lillio()
    return LillioClient.from_config(cfg)


def cmd_sync(cfg, args, alerter, state) -> int:
    from .sync import run_sync

    cfg.require_immich()
    result = run_sync(cfg, _lillio(cfg), ImmichClient(cfg.immich_url, cfg.immich_api_key), state, alerter,
                      dry_run=args.dry_run, limit=args.limit, full=args.full)
    return 1 if result.failed else 0


RUN_STATUS = "run-status.json"


def guarded_sync(cfg, args, alerter, state) -> int:
    """One sync run that alerts instead of raising: run_failed on error, run_recovered on the first good run after."""
    status = state.load(RUN_STATUS)
    try:
        rc = cmd_sync(cfg, args, alerter, state)
    except ConfigError:
        raise
    except Exception as e:  # noqa: BLE001
        msg = scrub(f"{type(e).__name__}: {e}", cfg.secrets())[:500]
        if not args.dry_run:
            since = status.get("failing_since") or datetime.now(cfg.tz).isoformat(timespec="minutes")
            alerter.send(A.RUN_FAILED, f"lillio-immich-sync sync failed (failing since {since}): {msg}")
            state.save(RUN_STATUS, {"failing_since": since})
        print(f"sync failed: {msg}", file=sys.stderr, flush=True)
        return 1
    if status.get("failing_since") and not args.dry_run:
        alerter.send(A.RUN_RECOVERED, f"lillio-immich-sync recovered (was failing since {status['failing_since']}).")
        state.save(RUN_STATUS, {})
    return rc


def cmd_alert_test(cfg, args, alerter, state) -> int:
    if not alerter.enabled:
        print("no alert channel configured; set ALERT_WEBHOOK_URL and/or ALERT_COMMAND", file=sys.stderr)
        return 1
    alerter.send(A.TEST, "lillio-immich-sync test alert: alert delivery works.")
    print("test alert sent; check your channel (delivery errors, if any, are printed above)")
    return 0


def cmd_daemon(cfg, args, alerter, state, *, now=None, sleep=time.sleep, max_runs: int = 0) -> int:
    """Stay running and sync on the SYNC_TIMES / SYNC_DAYS schedule, in TIMEZONE."""
    from .schedule import DAY_NAMES, next_run

    cfg.require_immich()
    cfg.require_lillio()
    now = now or (lambda: datetime.now(cfg.tz))
    print(f"schedule: {', '.join(t.strftime('%H:%M') for t in cfg.sync_times)} on "
          f"{','.join(DAY_NAMES[d] for d in sorted(cfg.sync_days))} ({cfg.timezone})", flush=True)
    runs = 0
    if args.run_now:
        guarded_sync(cfg, args, alerter, state)
        runs += 1
    while not max_runs or runs < max_runs:
        due = next_run(now(), cfg.sync_times, cfg.sync_days)
        print(f"next sync {due.isoformat()}", flush=True)
        while (remaining := (due - now()).total_seconds()) > 0:
            sleep(min(remaining, 300))  # wake periodically so clock changes and suspends are tolerated
        guarded_sync(cfg, args, alerter, state)
        runs += 1
    return 0


def cmd_children(cfg, args, alerter, state) -> int:
    kids = _lillio(cfg).children()
    for c in kids:
        print(f"{c.id}  {c.name}")
    if not kids:
        print("no children found; find the id in the portal URL /accounts/<id> and set LILLIO_CHILD_IDS",
              file=sys.stderr)
        return 1
    return 0


def shape(value, depth: int = 0):
    """Structure of a JSON value with content removed: strings become their type, URLs keep host and path only."""
    if depth > 12:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1), f"... {len(value)} items"] if value else []
    if isinstance(value, str) and value.startswith("http"):
        u = urlsplit(value)
        return f"<url {u.hostname}{'/…' + u.path[-12:] if u.path else ''}>"
    return f"<{type(value).__name__}>"


def cmd_probe(cfg, args, alerter, state) -> int:
    """Print the journal_api response structure (no captions, names or signed URLs) to debug portal changes."""
    lillio = _lillio(cfg)
    child_ids = list(cfg.lillio_child_ids) or [c.id for c in lillio.children()]
    if not child_ids:
        print("no children found; set LILLIO_CHILD_IDS", file=sys.stderr)
        return 1
    data = lillio.journal_page(child_ids[0], 1)
    print(json.dumps(shape(data), indent=1))
    count, items = lillio.journal(child_ids[0], 1)
    print(f"\npage 1: {count} activities, {len(items)} media items "
          f"({sum(i.kind == 'image' for i in items)} images, {sum(i.kind == 'video' for i in items)} videos)")
    for i in items[:5]:
        print(f"  {i.created_at.isoformat()}  {i.kind:5}  .{i.extension}  caption {len(i.caption)} chars")
    return 0


def cmd_list_albums(cfg, args, alerter, state) -> int:
    cfg.require_immich()
    for a in ImmichClient(cfg.immich_url, cfg.immich_api_key).albums():
        print(f"{a.get('assetCount', 0):>7}  {a['id']}  {a['albumName']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lillio-immich-sync", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--env-file", help="KEY=VALUE file; real environment variables take precedence")
    p.add_argument("--state-dir", help="override STATE_DIR")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="copy new photos and videos from Lillio into Immich")
    s.add_argument("--dry-run", action="store_true", help="show what would be uploaded; upload nothing")
    s.add_argument("--limit", type=int, default=0, help="upload at most N items")
    s.add_argument("--full", action="store_true", help="scan the whole journal instead of stopping at synced items")
    d = sub.add_parser("daemon", help="stay running and sync on the SYNC_TIMES / SYNC_DAYS schedule")
    d.add_argument("--run-now", action="store_true", help="also sync once immediately at startup")
    d.set_defaults(dry_run=False, limit=0, full=False)
    sub.add_parser("setup", help="prompt for logins, save them to the env file (default .env), and test them")
    sub.add_parser("children", help="list children visible to the Lillio account (id, name)")
    sub.add_parser("probe", help="print the journal response structure, content redacted (debugging)")
    sub.add_parser("alert-test", help="send a test alert through the configured channels")
    sub.add_parser("list-albums", help="list Immich albums (count, id, name)")
    return p


COMMANDS = {"sync": guarded_sync, "alert-test": cmd_alert_test, "daemon": cmd_daemon, "children": cmd_children, "probe": cmd_probe, "list-albums": cmd_list_albums}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "setup":
        from .setup import run_setup

        return run_setup(args.env_file or ".env")
    try:
        env = load_env_file(args.env_file) if args.env_file else dict(os.environ)
        if args.state_dir:
            env["STATE_DIR"] = args.state_dir
        cfg = Config.from_env(env)
    except (ConfigError, OSError) as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    alerter = A.Alerter.from_config(cfg)
    state = State(cfg.state_dir)
    try:
        return COMMANDS[args.command](cfg, args, alerter, state)
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        msg = scrub(f"{type(e).__name__}: {e}", cfg.secrets())[:500]
        print(f"{args.command} failed: {msg}", file=sys.stderr)
        return 1
