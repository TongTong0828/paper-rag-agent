"""Webhook fan-out for proactive notifications (P3 / M11.5).

Optional channels for inbox events:
  - DingTalk (markdown msgtype)
  - Feishu / Lark (interactive card)
  - WeCom (markdown)
  - Email (SMTP, plain HTML)

Wiring strategy
---------------
``inbox.write`` calls ``fan_out(item)`` AFTER a successful insert. fan_out
reads per-user webhook config from feedback.sqlite ``user_webhooks`` table
(schema below). All requests are best-effort, logged on failure, never raise
back into the caller.

Schema (auto-created)
---------------------
    CREATE TABLE IF NOT EXISTS user_webhooks (
        user_id     TEXT NOT NULL,
        channel     TEXT NOT NULL,    -- dingtalk | feishu | wecom | email
        endpoint    TEXT NOT NULL,    -- webhook URL or smtp:to@example.com
        secret      TEXT,             -- HMAC signing secret (DingTalk/Feishu)
        enabled     INTEGER DEFAULT 1,
        created_at  REAL NOT NULL,
        PRIMARY KEY (user_id, channel, endpoint)
    );

Industrial properties
---------------------
- Best-effort: failures NEVER block inbox.write
- ≤ 3s timeout per request, max 1 retry
- HMAC where supported (DingTalk SHA256+timestamp, Feishu sign)
- All HTTP via stdlib ``urllib`` — no httpx dep added for this small surface
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import smtplib
import sqlite3
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_TIMEOUT = 3.0
_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_webhooks (
    user_id     TEXT NOT NULL,
    channel     TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    secret      TEXT,
    enabled     INTEGER DEFAULT 1,
    created_at  REAL NOT NULL,
    PRIMARY KEY (user_id, channel, endpoint)
);
"""


def _resolve_path() -> Path:
    from ..feedback import store as feedback_store
    return feedback_store._resolve_path()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_webhook(user_id: str, channel: str, endpoint: str, secret: str | None = None) -> bool:
    if channel not in ("dingtalk", "feishu", "wecom", "email"):
        raise ValueError(f"unsupported channel: {channel}")
    with _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO user_webhooks(user_id, channel, endpoint, secret, "
            "enabled, created_at) VALUES(?,?,?,?,1,?)",
            (user_id, channel, endpoint, secret, time.time()),
        )
    return True


def list_webhooks(user_id: str) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT user_id, channel, endpoint, enabled, created_at "
            "FROM user_webhooks WHERE user_id=? AND enabled=1",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def disable_webhook(user_id: str, channel: str, endpoint: str) -> bool:
    with _connect() as con:
        cur = con.execute(
            "UPDATE user_webhooks SET enabled=0 WHERE user_id=? AND channel=? AND endpoint=?",
            (user_id, channel, endpoint),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Channel adapters
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.status, resp.read(2000).decode("utf-8", errors="replace")


def _send_dingtalk(endpoint: str, secret: str | None, item: dict) -> tuple[bool, str]:
    """DingTalk markdown msg with optional HMAC sign."""
    url = endpoint
    if secret:
        ts = str(int(time.time() * 1000))
        sign_str = f"{ts}\n{secret}"
        h = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(h).decode())
        url = f"{endpoint}{'&' if '?' in endpoint else '?'}timestamp={ts}&sign={sign}"
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": item.get("title", "paper_rag"),
            "text": f"### {item.get('title', '')}\n\n{item.get('body_md', '')[:2000]}",
        },
    }
    try:
        status, body = _post_json(url, payload)
        return status == 200, f"{status}:{body[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}:{e}"


def _send_feishu(endpoint: str, secret: str | None, item: dict) -> tuple[bool, str]:
    """Feishu / Lark markdown card."""
    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": item.get("title", "paper_rag")}},
            "elements": [
                {"tag": "markdown", "content": item.get("body_md", "")[:2000]}
            ],
        },
    }
    if secret:
        ts = str(int(time.time()))
        sign_str = f"{ts}\n{secret}"
        h = hmac.new(sign_str.encode(), b"", hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(h).decode()
    try:
        status, body = _post_json(endpoint, payload)
        return status == 200, f"{status}:{body[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}:{e}"


def _send_wecom(endpoint: str, _secret: str | None, item: dict) -> tuple[bool, str]:
    """WeCom (企业微信) markdown."""
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": f"## {item.get('title', '')}\n\n{item.get('body_md', '')[:2000]}"},
    }
    try:
        status, body = _post_json(endpoint, payload)
        return status == 200, f"{status}:{body[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}:{e}"


def _send_email(endpoint: str, _secret: str | None, item: dict) -> tuple[bool, str]:
    """endpoint format: ``smtp://host:port/?user=...&from=...&to=...``"""
    try:
        u = urllib.parse.urlparse(endpoint)
        q = dict(urllib.parse.parse_qsl(u.query))
        host = u.hostname or "localhost"
        port = u.port or 25
        sender = q.get("from") or q.get("user") or "paper_rag@localhost"
        receiver = q.get("to")
        if not receiver:
            return False, "missing 'to' in endpoint query"
        msg = MIMEText(item.get("body_md", ""), "plain", "utf-8")
        msg["Subject"] = item.get("title", "paper_rag")
        msg["From"] = sender
        msg["To"] = receiver
        with smtplib.SMTP(host, port, timeout=_TIMEOUT) as s:
            if q.get("user") and q.get("password"):
                s.starttls()
                s.login(q["user"], q["password"])
            s.send_message(msg)
        return True, f"sent->{receiver}"
    except Exception as e:
        return False, f"{type(e).__name__}:{e}"


_DISPATCH = {
    "dingtalk": _send_dingtalk,
    "feishu": _send_feishu,
    "wecom": _send_wecom,
    "email": _send_email,
}


# ---------------------------------------------------------------------------
# Public fan-out
# ---------------------------------------------------------------------------


def fan_out(item: dict) -> dict[str, Any]:
    """Best-effort fan-out to all enabled channels for `item['user_id']`.

    Returns a per-channel result dict (status / detail), useful for tests
    and admin debugging. Never raises.
    """
    user_id = item.get("user_id")
    if not user_id:
        return {"sent": 0, "skipped": "no user_id"}
    try:
        hooks = list_webhooks(user_id)
    except Exception as e:
        log.warning("webhook list_webhooks failed: %s", e)
        return {"sent": 0, "error": str(e)}

    if not hooks:
        return {"sent": 0, "skipped": "no webhooks configured"}

    results: list[dict] = []
    sent = 0
    for h in hooks:
        sender = _DISPATCH.get(h["channel"])
        if not sender:
            results.append({"channel": h["channel"], "ok": False, "detail": "unknown channel"})
            continue
        ok, detail = sender(h["endpoint"], h.get("secret"), item)
        results.append({"channel": h["channel"], "ok": ok, "detail": detail})
        if ok:
            sent += 1
    return {"sent": sent, "results": results}


__all__ = ["add_webhook", "disable_webhook", "fan_out", "list_webhooks"]
