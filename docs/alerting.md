# Alerting

No notification service is assumed. Configure a JSON webhook, a command, or both.

## Kinds

| Kind | When |
|---|---|
| `run_failed` | The run could not complete: login failed, the portal format changed, Immich unreachable. Says since when it has been failing |
| `run_recovered` | The first successful run after one or more failed runs |
| `items_failed` | Some photos or videos failed; the rest were synced. Sent once per distinct set of failures |
| `journal_stale` | The newest journal item is older than `STALE_ALERT_DAYS` (off by default). Once per quiet spell, re-armed when new items appear. Catches a portal change that makes the journal look empty instead of failing |
| `test` | Sent by the `alert-test` command |

Run `lillio-immich-sync alert-test` after configuring a channel to confirm delivery.

Failed items are not marked as synced, so the next run retries them.

## Webhook

`ALERT_WEBHOOK_URL` receives a POST of `ALERT_WEBHOOK_TEMPLATE` with `$MESSAGE` and `$KIND` substituted
(JSON-escaped, without surrounding quotes). The result must be valid JSON.

Keep the single quotes shown below: docker compose expands `$MESSAGE` itself in unquoted or double-quoted
`env_file` values, which would leave the alert text blank.

```bash
ALERT_WEBHOOK_TEMPLATE='{"text": "$MESSAGE"}'        # Slack-style (default)
ALERT_WEBHOOK_TEMPLATE='{"content": "$MESSAGE"}'     # Discord-style
ALERT_WEBHOOK_TEMPLATE='{"title": "lillio-immich-sync", "message": "$MESSAGE", "kind": "$KIND"}'
```

## Command

`ALERT_COMMAND` is split shell-style (no shell is invoked), receives the message on stdin and in `ALERT_MESSAGE`,
and the kind in `ALERT_KIND`. Example:

```bash
ALERT_COMMAND=/usr/local/bin/notify-me --title lillio
```

Configured secrets (passwords, cookie, API key, webhook URL) are scrubbed from error text before it is sent.
Alert delivery never raises: a broken alert channel does not break a sync.
