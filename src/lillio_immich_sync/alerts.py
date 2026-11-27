"""Generic alert delivery. No vendor is assumed: use a JSON webhook, a command, or both.

Webhook: POST ALERT_WEBHOOK_TEMPLATE with $MESSAGE (JSON-escaped, no surrounding quotes) and $KIND substituted.
  Slack-style (default):  {"text": "$MESSAGE"}
  Discord-style:          {"content": "$MESSAGE"}
Command: ALERT_COMMAND is split with shlex (no shell), receives the message on stdin and in ALERT_MESSAGE,
  and the alert kind in ALERT_KIND.
Delivery never raises; a broken alert channel must not break a sync.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from typing import Callable

import requests

# Alert kinds (stable identifiers for filtering in your own command or template)
RUN_FAILED = "run_failed"
ITEMS_FAILED = "items_failed"

MAX_LEN = 1800


def render_template(template: str, kind: str, message: str) -> str:
    """Substitute $MESSAGE and $KIND, JSON-escaping the values, and verify the result is valid JSON."""
    esc = lambda s: json.dumps(s)[1:-1]  # noqa: E731
    body = template.replace("$MESSAGE", esc(message)).replace("$KIND", esc(kind))
    json.loads(body)  # raises ValueError for a broken template
    return body


class Alerter:
    def __init__(
        self,
        webhook_url: str = "",
        template: str = '{"text": "$MESSAGE"}',
        command: str = "",
        *,
        post: Callable = requests.post,
        run: Callable = subprocess.run,
    ):
        self.webhook_url, self.template, self.command = webhook_url, template, command
        self._post, self._run = post, run

    @classmethod
    def from_config(cls, cfg) -> "Alerter":
        return cls(cfg.alert_webhook_url, cfg.alert_webhook_template, cfg.alert_command)

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url or self.command)

    def send(self, kind: str, message: str) -> None:
        message = message[:MAX_LEN]
        if self.webhook_url:
            try:
                body = render_template(self.template, kind, message)
                self._post(self.webhook_url, data=body, headers={"Content-Type": "application/json"}, timeout=15)
            except Exception as e:  # noqa: BLE001
                print(f"alert webhook failed: {type(e).__name__}", file=sys.stderr)
        if self.command:
            try:
                env = dict(os.environ, ALERT_KIND=kind, ALERT_MESSAGE=message)
                self._run(shlex.split(self.command), input=message, text=True, env=env, timeout=30, check=False)
            except Exception as e:  # noqa: BLE001
                print(f"alert command failed: {type(e).__name__}", file=sys.stderr)
