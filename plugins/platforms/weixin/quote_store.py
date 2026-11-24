"""Profile-local storage for Weixin server message IDs and native quotes."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeixinQuote:
    direction: str
    body: str
    media: list[dict[str, str]]


class WeixinQuoteStore:
    def __init__(self, hermes_home: str | Path, *, retention_days: int = 30, max_messages_per_account: int = 10_000):
        self._path = Path(hermes_home) / "weixin" / "quote_cache.db"
        self._retention_seconds = retention_days * 86_400
        self._max_messages_per_account = max_messages_per_account
        self._write_lock = threading.Lock()

    def put(
        self, account_id: str, chat_id: str, message_id: str, *, direction: Literal["inbound", "outbound"],
        body: str = "", media: Optional[list[dict[str, str]]] = None,
    ) -> bool:
        try:
            with self._write_lock:
                self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if not self._path.exists():
                    self._path.touch(mode=0o600)
                with closing(sqlite3.connect(self._path)) as conn, conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("""CREATE TABLE IF NOT EXISTS weixin_quotes (
                        account_id TEXT NOT NULL,
                        chat_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        body TEXT NOT NULL DEFAULT '',
                        media_json TEXT NOT NULL DEFAULT '[]',
                        created_at REAL NOT NULL,
                        PRIMARY KEY (account_id, chat_id, message_id)
                    )""")
                    conn.execute("CREATE INDEX IF NOT EXISTS weixin_quotes_created ON weixin_quotes(account_id, created_at)")
                    now = time.time()
                    conn.execute(
                        "INSERT OR REPLACE INTO weixin_quotes VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (account_id, chat_id, str(message_id), direction, body[:65_536], json.dumps(media or []), now),
                    )
                    conn.execute(
                        "DELETE FROM weixin_quotes WHERE account_id = ? AND created_at < ?",
                        (account_id, now - self._retention_seconds),
                    )
                    conn.execute("""DELETE FROM weixin_quotes WHERE rowid IN (
                        SELECT rowid FROM weixin_quotes WHERE account_id = ?
                        ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?
                    )""", (account_id, self._max_messages_per_account))
            return True
        except Exception as exc:
            logger.warning("Weixin quote cache write failed (%s)", type(exc).__name__)
            return False

    def find(self, account_id: str, chat_id: str, message_id: str) -> Optional[WeixinQuote]:
        try:
            if not self._path.exists():
                return None
            with closing(sqlite3.connect(self._path)) as conn:
                row = conn.execute(
                    """SELECT direction, body, media_json FROM weixin_quotes
                    WHERE account_id = ? AND chat_id = ? AND message_id = ? AND created_at >= ?""",
                    (account_id, chat_id, str(message_id), time.time() - self._retention_seconds),
                ).fetchone()
            return WeixinQuote(row[0], row[1], json.loads(row[2])) if row else None
        except Exception as exc:
            logger.warning("Weixin quote cache read failed (%s)", type(exc).__name__)
            return None


def nth_index_of(text: str, value: str, occurrence: int, from_index: int = 0) -> int:
    if not value or type(occurrence) is not int or occurrence < 0:
        return -1
    position = from_index
    for current in range(occurrence + 1):
        position = text.find(value, position)
        if position < 0:
            return -1
        if current < occurrence:
            position += len(value)
    return position


def resolve_partial_quote(full_text: str, partial: dict[str, Any]) -> tuple[Optional[str], bool]:
    """Resolve both iLink end-index variants, preferring global when no hash is supplied."""
    start, end = partial.get("start"), partial.get("end")
    if not full_text or not isinstance(start, str) or not start or not isinstance(end, str) or not end:
        return None, True
    start_pos = nth_index_of(full_text, start, partial.get("startindex"))
    if start_pos < 0:
        return None, True
    candidates = []
    for from_index in (0, start_pos + len(start)):
        end_pos = nth_index_of(full_text, end, partial.get("endindex"), from_index)
        if end_pos >= start_pos:
            candidate = full_text[start_pos:end_pos + len(end)]
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    expected = partial.get("quotemd5")
    if not expected:
        return (candidates[0], False) if candidates else (None, True)
    if isinstance(expected, str):
        for candidate in candidates:
            if hashlib.md5(candidate.encode("utf-8"), usedforsecurity=False).hexdigest() == expected.lower():
                return candidate, False
    return None, True
