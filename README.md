# lillio-immich-sync

Copy the photos and videos your daycare posts to [Lillio](https://www.lillio.com/) (formerly HiMama) into your
[Immich](https://immich.app/) library, with the right dates and the teacher's caption. Run it on a schedule and new
photos appear in Immich the same day. The first run backfills the whole journal.

> **Unofficial.** Lillio publishes no parent API. This tool reads the same pages and JSON that the parent web portal
> uses. They can change or be blocked at any time, and using them may conflict with the provider's terms of service.
> Use your own account, at your own risk. Not affiliated with Lillio or Immich.

## What it does

- **Finds every child** on the account (or the ones you list) and pages through each child's photo journal.
- **Uploads originals** to Immich: the largest image size the portal offers, and videos (can be turned off).
- **Keeps the context**: the photo's date in Immich is when the teacher posted it, and the activity title and
  description become the Immich description. The portal's stock caption ("Look what I'm doing today!") is dropped.
- **Optionally files them in albums**: one per child (`IMMICH_ALBUM=Daycare - {child}`) and any number of extra
  albums, such as an existing shared family album (`IMMICH_EXTRA_ALBUMS`).
- **Never uploads twice**: a local record of synced items, plus Immich's own duplicate detection as a backstop.
  After the first full pass, runs stop paging as soon as they reach photos already synced.
- **Isolates failures**: one broken file does not stop the rest, and it is retried next run.
- **Alerts** through generic hooks (a JSON webhook and/or any command) when a run fails or items fail.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env                         # optional settings: album, schedule, alerts
lillio-immich-sync --env-file .env setup           # prompts for logins, saves them (mode 600), tests them
lillio-immich-sync --env-file .env children        # check login, list children
lillio-immich-sync --env-file .env probe           # check the journal format (content redacted)
lillio-immich-sync --env-file .env sync --dry-run  # see what would be uploaded
lillio-immich-sync --env-file .env sync --limit 1  # upload one item as a test
lillio-immich-sync --env-file .env sync            # full backfill
lillio-immich-sync --env-file .env daemon          # stay running, sync daily at SYNC_TIMES
```

Create the Immich API key under *Account Settings > API Keys* with `asset.upload` and `asset.update`, plus
`album.read`, `album.create` and `albumAsset.create` if you use albums. To add to an album shared with you, the key's
user needs editor access to it. Photos land in the library of the
user who owns the key.

Some daycare chains run Lillio under their own name and host. Set `LILLIO_BASE_URL` to the address you sign in at.

## Commands

| Command | Purpose |
|---|---|
| `sync [--dry-run] [--limit N] [--full]` | Copy new items into Immich. `--full` rescans the whole journal |
| `daemon [--run-now]` | Stay running and sync on the `SYNC_TIMES` / `SYNC_DAYS` schedule (alerts, never exits on a failed run) |
| `setup` | Prompt for logins and URLs, save them to the env file (mode 600), and test both logins |
| `children` | List children visible to the account (id, name) |
| `probe` | Print the journal response structure with captions, names and signed URLs removed |
| `list-albums` | List Immich albums (count, id, name) |

Exit codes (`sync`): 0 success, 1 run failed or some items failed, 2 configuration error.

## Configuration

Environment variables (or an `--env-file`; real environment variables win).

| Variable | Default | Meaning |
|---|---|---|
| `LILLIO_BASE_URL` | `https://app.lillio.com` | Portal address you sign in at |
| `LILLIO_EMAIL`, `LILLIO_PASSWORD` | | Parent account credentials |
| `LILLIO_SESSION_COOKIE` | | Instead of a password: `name=value`, or a bare value for `_himama_session` |
| `LILLIO_CHILD_IDS` | discovered | Comma-separated child ids to sync |
| `TIMEZONE` | `UTC` | Your local zone (e.g. `America/New_York`): the schedule, and portal timestamps without an offset |
| `IMMICH_URL` | | Base URL of your Immich server |
| `IMMICH_API_KEY` | | API key (see permissions above) |
| `IMMICH_ALBUM` | none | Album name; `{child}` is replaced by the child's name |
| `IMMICH_EXTRA_ALBUMS` | none | More albums to add every item to, separated by `;` (e.g. an existing shared album). `{child}` works here too |
| `IMMICH_CREATE_ALBUMS` | `1` | Create albums that do not exist. Set `0` to require them to exist, so a typo stops the run instead of creating a stray album |
| `SYNC_VIDEOS` | `1` | Set `0` to skip videos |
| `SYNC_SINCE` | | Ignore items older than this date (`YYYY-MM-DD`) |
| `SYNC_TIMES` | `18:00` | `daemon` run times, local, comma separated (e.g. `07:30,18:00`) |
| `SYNC_DAYS` | `daily` | `daemon` run days: `daily`, `weekdays`, `weekends`, `mon-fri`, or `mon,wed,fri` |
| `STATE_DIR` | `./state` | Where the record of synced items lives |
| `ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_TEMPLATE` | | Generic JSON webhook, see [docs/alerting.md](docs/alerting.md) |
| `ALERT_COMMAND` | | Command run for each alert (message on stdin) |

## Scheduling and deployment

Two ways: run `daemon` (in a container or as a service) and set the schedule with `SYNC_TIMES` and `SYNC_DAYS`,
or run `sync` from cron or a systemd timer. See [docs/deployment.md](docs/deployment.md).

## Security notes

- Keep `.env` mode 600 and out of version control (`.gitignore` already excludes it).
- Media files are downloaded without sending portal cookies to the storage host.
- `probe` output contains no captions, names or signed URLs, so it is safe to paste into a bug report.
- Failure alerts scrub configured secrets from error text before sending.

## Limitations

- Depends on undocumented portal behaviour; see [docs/lillio-api.md](docs/lillio-api.md) for exactly what, and
  what is still unverified. A format change makes runs fail loudly (and alert) rather than silently sync nothing.
- The date is when the item was posted, which can be later than when it was taken. If the file itself carries a
  camera date, Immich uses that instead.
- Photos you delete in Immich are not re-added (the local record remembers them). Use `--full` with a fresh
  `STATE_DIR` to start over.
- Only photos and videos are copied. Daily reports (naps, meals, diapers) are out of scope.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests use fakes for Lillio and Immich; nothing touches the network.

## Credits and prior art

Nothing here copies their code; the facts they surfaced shaped the client.

- **[tkuppens/kindercare](https://github.com/tkuppens/kindercare)**: documented the JSON journal endpoint and its
  fields.
- **[meagar/himama-dl](https://github.com/meagar/himama-dl)** (MIT): login flow, child discovery and the HTML
  activities page.
- Libraries: [requests](https://requests.readthedocs.io/),
  [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/). The Immich REST API is documented by the Immich
  project.

## License

MIT, see [LICENSE](LICENSE).
