# Deployment

Daycare photos arrive during the day, so one run in the early evening is usually enough. There are two ways to
schedule it.

## Option 1: built-in schedule (`daemon`)

`daemon` stays running and syncs at `SYNC_TIMES` on `SYNC_DAYS`, in `TIMEZONE`. A failed run sends an alert and the
daemon keeps going; it logs in afresh for every run.

```bash
TIMEZONE=America/New_York
SYNC_TIMES=18:00          # or several: 12:00,18:00
SYNC_DAYS=daily           # or weekdays, mon-fri, mon,wed,fri
```

### Container (docker compose)

```yaml
services:
  lillio-immich-sync:
    build: .            # or an image you built and pushed
    env_file: .env
    volumes:
      - state:/state
    restart: unless-stopped
volumes:
  state:
```

The image runs `daemon` by default. Add `command: ["daemon", "--run-now"]` to also sync once at startup.

The `/state` volume must persist: it records which journal items are already in Immich. Losing it means the next
run re-scans the whole journal and re-downloads; Immich recognises identical files as duplicates and does not store
them twice, so the cost is time, not duplicate photos.

### systemd service

```ini
[Unit]
Description=Copy Lillio photos into Immich
After=network-online.target

[Service]
WorkingDirectory=/opt/lillio-immich-sync
EnvironmentFile=/opt/lillio-immich-sync/.env
ExecStart=/opt/lillio-immich-sync/.venv/bin/lillio-immich-sync daemon
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Option 2: external scheduler (`sync`)

`sync` performs one run and exits: 0 on success, 1 if the run or any item failed. `SYNC_TIMES` and `SYNC_DAYS` are
ignored; the scheduler decides when.

### cron

```cron
0 18 * * * cd /opt/lillio-immich-sync && .venv/bin/lillio-immich-sync --env-file .env sync >> run.log 2>&1
```

Use `flock -n /tmp/lis.lock -c '...'` if a run could outlast the interval (the first backfill can take a while).

### One-shot container

```bash
docker run --rm --env-file .env -v lis-state:/state lillio-immich-sync sync
```

## First run checklist

1. `setup` to enter and test the logins (it also lists the child ids).
2. `probe` to confirm the journal format matches [lillio-api.md](lillio-api.md).
3. `list-albums` to confirm the Immich key works.
4. `sync --dry-run`, then `sync --limit 1`, and check the photo in Immich (date, caption, album).
5. `sync` for the full backfill, then start `daemon` (or schedule `sync`).
6. Send yourself a test alert, for example by running once with a wrong `LILLIO_PASSWORD`.
