"""The conversation store: encrypted SQLite, kept until you delete it (D5, F9).

What is persisted, and nothing else:

- the questions you actually triggered,
- the answers — including the *spoken prefix* and how far speech got when you
  interrupted (§5.4.1, D12a),
- a snapshot of the ambient context that was actually sent, so the chat window
  can show you exactly what left the machine.

The ambient buffer itself is never written here. Titles and timestamps are
plaintext so the history list renders without decrypting everything.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from bgassist.storage.crypto import NullCipher

log = logging.getLogger("bgassist.storage.conversations")

#: A voice exchange continues the most recent conversation if it is younger
#: than this, otherwise it starts a new one (§7).
CONTINUATION_SECONDS = 600.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts    REAL NOT NULL,
    updated_ts    REAL NOT NULL,
    title         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    ts              REAL NOT NULL,
    body            BLOB,
    context         BLOB,
    spoken_upto     INTEGER,
    interrupted     INTEGER NOT NULL DEFAULT 0,
    superseded      INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'voice'
);
CREATE INDEX IF NOT EXISTS messages_conversation
    ON messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS conversations_updated
    ON conversations (updated_ts DESC);
PRAGMA user_version = 1;
"""


@dataclass
class Message:
    id: int
    conversation_id: int
    role: str
    ts: float
    text: str
    context: str = ""
    spoken_upto: Optional[int] = None
    interrupted: bool = False
    superseded: bool = False
    source: str = "voice"

    @property
    def full_text(self) -> str:
        return self.text

    @property
    def spoken_text(self) -> str:
        """What the user actually heard — the half that goes to the model."""
        if self.role == "assistant" and self.interrupted and self.spoken_upto is not None:
            return self.text[:self.spoken_upto]
        return self.text

    @property
    def unspoken_text(self) -> str:
        """The remainder, shown dimmed behind a disclosure in the chat window."""
        if self.role == "assistant" and self.interrupted and self.spoken_upto is not None:
            return self.text[self.spoken_upto:]
        return ""


@dataclass
class Conversation:
    id: int
    created_ts: float
    updated_ts: float
    title: str = ""


class ConversationStore:
    def __init__(self, path: Optional[Path] = None, cipher=None,
                 continuation_seconds: float = CONTINUATION_SECONDS):
        if path is None:
            from bgassist.platform import paths

            path = paths.conversations_db()
        self.path = Path(path)
        self.cipher = cipher or NullCipher()
        self.continuation_seconds = float(continuation_seconds)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        try:
            if self.path.exists():
                self.path.chmod(0o600)
        except OSError:  # pragma: no cover
            pass

    # -- helpers ---------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def closed(self) -> bool:
        return self._closed

    def _usable(self) -> bool:
        """False once the store is closed.

        Background work outlives the store by design — auto-titling is a second
        model call that runs after the answer, and quitting mid-flight is
        normal. Writing to a closed connection is not an error worth raising in
        a daemon thread; it is simply too late to matter.
        """
        if self._closed:
            log.debug("ignoring a request on a closed conversation store")
            return False
        return True

    def _decrypt(self, blob) -> str:
        if blob is None:
            return ""
        try:
            return self.cipher.decrypt(blob)
        except Exception as exc:  # noqa: BLE001 - wrong key, corrupt row
            log.error("could not decrypt a stored message: %s", exc)
            return "[unreadable — this message was encrypted with a different key]"

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"], conversation_id=row["conversation_id"], role=row["role"],
            ts=row["ts"], text=self._decrypt(row["body"]),
            context=self._decrypt(row["context"]),
            spoken_upto=row["spoken_upto"], interrupted=bool(row["interrupted"]),
            superseded=bool(row["superseded"]), source=row["source"])

    # -- conversations ---------------------------------------------------
    def create_conversation(self, now: Optional[float] = None, title: str = "") -> int:
        if not self._usable():
            return -1
        now = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations (created_ts, updated_ts, title) "
                "VALUES (?, ?, ?)", (now, now, title))
            self._conn.commit()
            return int(cur.lastrowid)

    def current_conversation(self, now: Optional[float] = None,
                             create: bool = True) -> Optional[int]:
        """The conversation a new voice exchange belongs to (the 10-minute rule)."""
        if not self._usable():
            return None
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT id, updated_ts FROM conversations "
                "ORDER BY updated_ts DESC LIMIT 1").fetchone()
        if row is not None and (now - row["updated_ts"]) <= self.continuation_seconds:
            return int(row["id"])
        return self.create_conversation(now) if create else None

    def touch(self, conversation_id: int, now: Optional[float] = None) -> None:
        if not self._usable():
            return
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET updated_ts = ? WHERE id = ?",
                (now, conversation_id))
            self._conn.commit()

    def set_title(self, conversation_id: int, title: str) -> None:
        if not self._usable():
            return
        with self._lock:
            self._conn.execute("UPDATE conversations SET title = ? WHERE id = ?",
                               (title.strip()[:120], conversation_id))
            self._conn.commit()

    def get_conversation(self, conversation_id: int) -> Optional[Conversation]:
        if not self._usable():
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
        return None if row is None else Conversation(
            id=row["id"], created_ts=row["created_ts"],
            updated_ts=row["updated_ts"], title=row["title"])

    def list_conversations(self, limit: int = 100) -> List[Conversation]:
        if not self._usable():
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations ORDER BY updated_ts DESC LIMIT ?",
                (limit,)).fetchall()
        return [Conversation(id=r["id"], created_ts=r["created_ts"],
                             updated_ts=r["updated_ts"], title=r["title"])
                for r in rows]

    def delete_conversation(self, conversation_id: int) -> None:
        if not self._usable():
            return
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE conversation_id = ?",
                               (conversation_id,))
            self._conn.execute("DELETE FROM conversations WHERE id = ?",
                               (conversation_id,))
            self._conn.commit()

    def delete_all(self) -> None:
        if not self._usable():
            return
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM conversations")
            self._conn.commit()

    def purge_older_than(self, days: float, now: Optional[float] = None) -> int:
        """Optional auto-delete (Preferences → Privacy). Off by default."""
        if not self._usable():
            return 0
        now = time.time() if now is None else now
        cutoff = now - days * 86400.0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM conversations WHERE updated_ts < ?",
                (cutoff,)).fetchall()
            ids = [int(r["id"]) for r in rows]
            for conversation_id in ids:
                self._conn.execute("DELETE FROM messages WHERE conversation_id = ?",
                                   (conversation_id,))
            self._conn.execute("DELETE FROM conversations WHERE updated_ts < ?",
                               (cutoff,))
            self._conn.commit()
        return len(ids)

    # -- messages --------------------------------------------------------
    def add_message(self, conversation_id: int, role: str, text: str, *,
                    context: str = "", ts: Optional[float] = None,
                    spoken_upto: Optional[int] = None, interrupted: bool = False,
                    superseded: bool = False, source: str = "voice") -> Message:
        ts = time.time() if ts is None else ts
        if not self._usable():
            return Message(id=-1, conversation_id=conversation_id, role=role,
                           ts=ts, text=text or "", context=context or "",
                           spoken_upto=spoken_upto, interrupted=interrupted,
                           superseded=superseded, source=source)
        body = self.cipher.encrypt(text or "")
        context_blob = self.cipher.encrypt(context or "") if context else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (conversation_id, role, ts, body, context, "
                "spoken_upto, interrupted, superseded, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, role, ts, body, context_blob, spoken_upto,
                 int(interrupted), int(superseded), source))
            self._conn.execute(
                "UPDATE conversations SET updated_ts = ? WHERE id = ?",
                (ts, conversation_id))
            self._conn.commit()
            message_id = int(cur.lastrowid)
        return Message(id=message_id, conversation_id=conversation_id, role=role,
                       ts=ts, text=text or "", context=context or "",
                       spoken_upto=spoken_upto, interrupted=interrupted,
                       superseded=superseded, source=source)

    def update_message(self, message_id: int, *, text: Optional[str] = None,
                       spoken_upto: Optional[int] = None,
                       interrupted: Optional[bool] = None,
                       superseded: Optional[bool] = None) -> None:
        if not self._usable():
            return
        sets, values = [], []
        if text is not None:
            sets.append("body = ?")
            values.append(self.cipher.encrypt(text))
        if spoken_upto is not None:
            sets.append("spoken_upto = ?")
            values.append(int(spoken_upto))
        if interrupted is not None:
            sets.append("interrupted = ?")
            values.append(int(interrupted))
        if superseded is not None:
            sets.append("superseded = ?")
            values.append(int(superseded))
        if not sets:
            return
        values.append(message_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", values)
            self._conn.commit()

    def messages(self, conversation_id: int, limit: int = 200) -> List[Message]:
        if not self._usable():
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? "
                "ORDER BY id ASC LIMIT ?", (conversation_id, limit)).fetchall()
        return [self._row_to_message(row) for row in rows]

    def history(self, conversation_id: int, turns: int = 12) -> List[dict]:
        """Chat turns for the next request — spoken prefixes only (D12a)."""
        from bgassist.llm.prompts import history_from_messages

        messages = self.messages(conversation_id)[-turns:]
        return history_from_messages(messages)

    def search(self, needle: str, limit: int = 50) -> List[Conversation]:
        """Search titles and bodies.

        Bodies are encrypted, so this decrypts as it goes rather than pushing
        the search into SQL. At the scale of one person's conversations that is
        entirely fine, and it is the price of the messages being unreadable
        without the keychain entry.
        """
        needle = (needle or "").strip().lower()
        if not needle:
            return self.list_conversations(limit)
        found: List[Conversation] = []
        for conversation in self.list_conversations(limit=500):
            if needle in (conversation.title or "").lower():
                found.append(conversation)
                continue
            if any(needle in message.text.lower()
                   for message in self.messages(conversation.id)):
                found.append(conversation)
            if len(found) >= limit:
                break
        return found

    def export_markdown(self, conversation_id: int) -> str:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return ""
        lines = [f"# {conversation.title or 'Conversation'}", ""]
        for message in self.messages(conversation_id):
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(message.ts))
            who = "You" if message.role == "user" else "Assistant"
            lines.append(f"**{who}** · {stamp}")
            lines.append("")
            lines.append(message.spoken_text.strip())
            if message.unspoken_text.strip():
                lines.append("")
                lines.append(f"> *(interrupted; unspoken: "
                             f"{message.unspoken_text.strip()})*")
            lines.append("")
        return "\n".join(lines)

    def count(self) -> dict:
        if not self._usable():
            return {"conversations": 0, "messages": 0}
        with self._lock:
            conversations = self._conn.execute(
                "SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
            messages = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        return {"conversations": int(conversations), "messages": int(messages)}
