"""reminders — the proactive side of the family-memory system (worker half).

Architecture boundary:
  - The pi extension (TS) OWNS the data: it writes reminders into memory/events.db.
  - This worker (Python) OWNS the voice: it polls that DB on a heartbeat and speaks up,
    because only the worker holds the LiveKit AgentSession (pi has no voice).
  - The contract between them is the shared SQLite file. Nothing else.

Two pieces:
  - EventStore : the same schema as events.ts (CREATE ... IF NOT EXISTS, so either side
                 may create the file first).
  - Deliverer  : the proactive protocol. LiveKit-free — it talks to the world through the
                 ProactiveIO duck type, so it is unit-testable with a fake clock/IO.

Protocol (approved by the user):
  1. When an event is due, call the user BY NAME AND SAY WHAT IT IS ABOUT — "Ayhan, bir
     hatırlatmam var." (never the content itself; that comes after the ack).
  2. Any reply counts as acknowledgement → deliver the reminder → mark `delivered`.
  3. No reply → call ONE more time; still nothing → the event stays `pending` (attempts++)
     and is retried later (RETRY_AFTER backoff). Not pushy, but never forgets.

Boundaries:
  - If the user is SPEAKING (or the assistant is answering) → the call is DEFERRED.
  - If the user is not in the room → no call at all; the event stays pending. On reconnect
    overdue events are delivered; if more than LATE_HOURS late, it is flagged as overdue.
  - Reminders fire EVEN WHILE ASLEEP (sleep must not mute them). Speaking up STARTS a
    conversation, so the INPUT side must open at the same moment: `wake()` is called
    BEFORE the first call-out — the user must NOT have to say the wake word to answer a
    question WE asked. `hold()` still routes that answer to US (not to pi → no double
    reply). Nobody answers after both attempts → `sleep()` puts us back where we were
    (staying awake for nothing burns tokens on every ambient word).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tunables (env-overridable).
HEARTBEAT_SECONDS = float(os.environ.get("PROACTIVE_TICK_SECONDS", "20") or 20)
REPLY_TIMEOUT = float(os.environ.get("PROACTIVE_REPLY_TIMEOUT", "8") or 8)
RETRY_AFTER = float(os.environ.get("PROACTIVE_RETRY_SECONDS", "300") or 300)
LATE_HOURS = float(os.environ.get("PROACTIVE_LATE_HOURS", "12") or 12)
# Onay sözünün BİTMESİ için üst sınır: VAD "konuşuyor" der demez onay saymak yetmez,
# cümlenin final transkripti gelene kadar beklenir — ama sonsuza kadar değil.
ACK_SETTLE_SECONDS = float(os.environ.get("PROACTIVE_ACK_SETTLE_SECONDS", "6") or 6)


def events_db_path() -> Path:
    """memory/events.db — the SAME path the pi extension uses (EVENTS_DB / MEM_DIR)."""
    p = os.environ.get("EVENTS_DB")
    if p:
        return Path(p)
    mem = os.environ.get("MEM_DIR") or os.environ.get("MEMORY_DIR") or "memory"
    return REPO_ROOT / mem / "events.db"  # an absolute `mem` wins (pathlib rule)


def _iso(ts: float) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _epoch(iso: str) -> float:
    return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()


@dataclass
class Event:
    id: int
    kind: str          # 'reminder' | 'task_done' — keeps the queue source-agnostic
    user: str
    text: str
    requested_at: str  # when it was asked for
    due_at: str        # when it should happen
    status: str        # what happened
    attempts: int

    @property
    def due_ts(self) -> float:
        return _epoch(self.due_at)


SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT NOT NULL,
  user         TEXT NOT NULL,
  text         TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  due_at       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  attempts     INTEGER NOT NULL DEFAULT 0,
  delivered_at TEXT,
  source       TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_due ON events(status, due_at);
"""


class EventStore:
    """events.db — schema shared with events.ts. Idempotent; created on first use."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else events_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass

    def add(self, kind: str, user: str, text: str, due_ts: float,
            now: Optional[float] = None, source: str = "worker") -> int:
        """Queue an event (source-agnostic: 'reminder' | 'task_done' | ...)."""
        now = time.time() if now is None else now
        cur = self.db.execute(
            "INSERT INTO events(kind,user,text,requested_at,due_at,status,attempts,source)"
            " VALUES(?,?,?,?,?,'pending',0,?)",
            (kind, user, text, _iso(now), _iso(due_ts), source),
        )
        return int(cur.lastrowid)

    def due(self, user: str, now: Optional[float] = None) -> list[Event]:
        """Pending events whose time has come (oldest first)."""
        now = time.time() if now is None else now
        rows = self.db.execute(
            "SELECT * FROM events WHERE user=? AND status='pending' AND due_at<=?"
            " ORDER BY due_at",
            (user, _iso(now)),
        ).fetchall()
        return [Event(**{k: r[k] for k in Event.__annotations__}) for r in rows]

    def mark_delivered(self, eid: int, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.db.execute(
            "UPDATE events SET status='delivered', delivered_at=?, attempts=attempts+1"
            " WHERE id=?",
            (_iso(now), eid),
        )

    def bump_attempt(self, eid: int) -> None:
        """No reply → the event STAYS pending; only the attempt counter moves."""
        self.db.execute("UPDATE events SET attempts=attempts+1 WHERE id=?", (eid,))

    def get(self, eid: int) -> Optional[Event]:
        r = self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return Event(**{k: r[k] for k in Event.__annotations__}) if r else None


class AckTracker:
    """The reply to a call-out — and the difference between "the user STARTED talking"
    and "the user FINISHED talking".

    Why they must not be conflated (this WAS the bug, seen live on events #11/#12):
    VAD (`user_state_changed → speaking`) fires on the first syllable; the final
    transcript lands SECONDS later (Whisper endpointing). Treating the first syllable
    as the acknowledgement and speaking immediately breaks the delivery TWICE:
      (a) we talk over the user → barge-in cuts the reminder → they never hear it;
      (b) `_deliver` finishes and drops `hold` BEFORE that transcript exists, so when
          it finally arrives the gate is open and pi answers it as if it were a brand
          new question ("Dinliyorum." → "Senin için yapabileceğim başka bir şey var mı?").
    So `wait()` waits for the sentence to be OVER: final transcript + VAD gone quiet.
    LiveKit-free on purpose (asyncio only) → unit-testable with a fake clock.
    """

    def __init__(self, settle: float = ACK_SETTLE_SECONDS):
        self.settle = settle
        self.seen = asyncio.Event()   # a reply exists (VAD, or any transcript)
        self.done = asyncio.Event()   # ...and the sentence is FINISHED (final + silence)
        self._final = False
        self._speaking = False

    def arm(self) -> None:
        """Reset for a new call-out. If the user is talking RIGHT NOW, that already
        counts as a reply (they barged into the call-out) — do not lose it."""
        self.seen.clear()
        self.done.clear()
        self._final = False
        if self._speaking:
            self.seen.set()

    def on_transcript(self, is_final: bool) -> None:
        self.seen.set()               # even a partial = they are answering us
        if is_final:
            self._final = True
            self._settle()

    def on_speaking(self, speaking: bool) -> None:
        self._speaking = bool(speaking)
        if speaking:
            self.seen.set()
        else:
            self._settle()            # VAD went quiet → maybe the sentence is over

    def _settle(self) -> None:
        if self._final and not self._speaking:
            self.done.set()

    async def wait(self, timeout: float) -> bool:
        """Wait for a reply; if one comes, wait for it to END. No reply at all → False."""
        self.arm()
        try:
            await asyncio.wait_for(self.seen.wait(), timeout)
        except asyncio.TimeoutError:
            return False              # silence → the caller calls out once more
        try:
            await asyncio.wait_for(self.done.wait(), self.settle)
        except asyncio.TimeoutError:
            pass                      # no final transcript (noise / VAD false positive):
            # speak anyway — if that speech gets cut, `_deliver` does NOT mark it
            # delivered, so the event stays pending and is retried. Never lost.
        return True


class ProactiveIO(Protocol):
    """The Deliverer's only window to the outside world (LiveKit ⟷ test fake)."""

    def present(self) -> bool: ...             # is the user in the room?
    def busy(self) -> bool: ...                # user speaking / assistant answering
    def display_name(self, user: str) -> str: ...
    def set_busy(self, v: bool) -> None: ...   # wake_agent_busy → freeze the sleep timer
    def hold(self, v: bool) -> None: ...       # during the exchange, don't route to pi
    def wake(self) -> bool: ...                # open the conversation window (True = newly)
    def sleep(self) -> None: ...               # close it again (nobody answered)
    # False = the speech was CUT (barge-in) → it was not heard. None/True = fully said.
    # `interruptible=False` = this sentence MUST be heard in full; the voice stack may
    # neither cut it nor answer whatever the user says over it (see `_deliver`).
    async def say(self, text: str, interruptible: bool = True) -> Optional[bool]: ...
    # True = the user replied AND finished their sentence (see AckTracker).
    async def wait_reply(self, timeout: float) -> bool: ...


# Call-out lines: name + WHAT IT IS ABOUT, never the content (that follows the ack).
# Rotated so a daily reminder does not sound like a recording. No LLM here on purpose:
# a call-out must be instant and free.
CALL_LINES: dict[str, tuple[str, ...]] = {
    "reminder": (
        "{name}, bir hatırlatmam var.",
        "{name}, sana bir şey hatırlatacaktım.",
        "{name}, bir hatırlatma için seslendim.",
    ),
    "task_done": (
        "{name}, bir işin bitti.",
        "{name}, sana bir haberim var.",
        "{name}, biten bir iş için seslendim.",
    ),
}


class Deliverer:
    """The proactive protocol. Pure async; the clock is injectable via `now_fn`."""

    def __init__(self, store: EventStore, io: ProactiveIO, *,
                 reply_timeout: float = REPLY_TIMEOUT,
                 retry_after: float = RETRY_AFTER,
                 late_hours: float = LATE_HOURS,
                 now_fn=time.time):
        self.store = store
        self.io = io
        self.reply_timeout = reply_timeout
        self.retry_after = retry_after
        self.late_hours = late_hours
        self.now = now_fn
        # In-memory backoff: an unanswered event must not be retried on the very next tick.
        self._backoff: dict[int, float] = {}
        self._call_i = 0          # call-out line rotation (no robotic repetition)
        self.log: list[str] = []  # recent actions (tests / observability)

    async def tick(self, user: str) -> int:
        """One heartbeat. Returns how many events were delivered."""
        if not user:
            return 0
        if not self.io.present():
            self.log.append("skip: user not in the room -> events stay pending")
            return 0  # nobody there → do NOT speak; the event waits
        now = self.now()
        n = 0
        for ev in self.store.due(user, now):
            if self.io.busy():
                self.log.append(f"defer#{ev.id}: conversation in progress -> wait our turn")
                break  # user speaking / assistant answering → never interrupt
            if self._backoff.get(ev.id, 0.0) > now:
                continue
            if await self._deliver(ev, now):
                n += 1
        return n

    async def _deliver(self, ev: Event, now: float) -> bool:
        io = self.io
        name = io.display_name(ev.user)
        io.hold(True)      # the user's ack goes to US, not to pi (no double answer)
        io.set_busy(True)  # fire even while asleep; freeze the sleep timer meanwhile
        # WE are starting this conversation → open the INPUT side too, exactly as if the
        # user had said the wake word. Making them wake us up to answer OUR question was
        # the bug: output was awake (say bypasses the gate), input was still asleep.
        woke = io.wake()
        try:
            for attempt in (1, 2):  # no reply → call ONE more time
                await io.say(self._call_line(ev, name))
                if await io.wait_reply(self.reply_timeout):
                    io.wake()  # keep the window open (idempotent; ack refreshes it)
                    # THE reminder. `interruptible=False` is the whole ballgame: the ack
                    # ("efendim, dinliyorum") ENDS a user turn, and a voice stack answers a
                    # finished turn by CUTTING whatever the agent is saying and replying to
                    # it. That is us, mid-reminder — the user hears the call-out, then an
                    # answer to "dinliyorum", and never the reminder. An uninterruptible
                    # sentence cannot be cut, and the turn that would have cut it is not
                    # answered at all. (The call-out above stays interruptible: barging in
                    # on it IS the ack, and it must reach STT.)
                    # Belt and braces: if it somehow still gets cut, they did NOT hear it →
                    # do not mark it delivered; retry it later.
                    if await io.say(self._message(ev, now), interruptible=False) is False:
                        self._retry_later(ev, now)
                        self.log.append(f"cut#{ev.id}: reminder interrupted -> still pending")
                        return False
                    self.store.mark_delivered(ev.id, now)
                    self._backoff.pop(ev.id, None)
                    self.log.append(f"delivered#{ev.id} (attempt {attempt})")
                    return True   # window stays OPEN → the user may just keep talking
            self._retry_later(ev, now)  # stays pending → never forgotten
            if woke:
                io.sleep()  # nobody answered → back to sleep (we woke it, we undo it)
            self.log.append(f"no-reply#{ev.id}: back to pending (attempts++)")
            return False
        finally:
            io.set_busy(False)
            io.hold(False)

    def _retry_later(self, ev: Event, now: float) -> None:
        """Not delivered (no reply, or the reminder was cut off) → the event STAYS
        pending and comes back after the backoff. A reminder is never dropped."""
        self.store.bump_attempt(ev.id)
        self._backoff[ev.id] = now + self.retry_after

    def _call_line(self, ev: Event, name: str) -> str:
        """The call-out: name + WHAT it is about. Never the content — that is the reward
        for answering (and keeps us from talking to an empty room)."""
        lines = CALL_LINES.get(ev.kind) or CALL_LINES["reminder"]
        line = lines[self._call_i % len(lines)]
        self._call_i += 1
        return line.format(name=name)

    def _message(self, ev: Event, now: float) -> str:
        """What Candan actually SAYS — Turkish (the user speaks Turkish)."""
        late_h = (now - ev.due_ts) / 3600.0
        body = (f"Şu iş bitti: {ev.text}" if ev.kind == "task_done"
                else f"Bana hatırlat demiştin: {ev.text}")
        if late_h > self.late_hours:
            return f"Kusura bakma, geç kaldım. {body}. Vakti geçmiş ama yine de söyleyeyim."
        return body + "."
