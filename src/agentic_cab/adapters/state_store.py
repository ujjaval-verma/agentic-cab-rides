"""SqliteStateStore — see spec §10a + CLAUDE.md inv. 5, 7, 11."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from agentic_cab.core.ports import ClaimResult, ConfirmationRequest, PendingConfirmationRecord
from agentic_cab.core.types import (
    AppointmentIntent,
    Booking,
    BookingEvent,
    Draft,
    Intent,
    Option,
    RideIntent,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SqliteStateStore:
    """SQLite-backed state store.

    Args:
        db_path: Path to the SQLite file. Parent directories are created on demand.
        check_same_thread: Passed directly to ``sqlite3.connect``. Set to ``False``
            for the FastAPI web path, which runs handlers on different asyncio
            threads. When ``False``, a ``threading.Lock`` serialises every write so
            concurrent writes do not corrupt the database. Read operations on SQLite
            are safe without locking because WAL mode allows concurrent readers.
    """

    def __init__(self: Self, db_path: Path, *, check_same_thread: bool = True) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=check_same_thread)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Serialise writes across threads when check_same_thread=False.
        self._write_lock: threading.Lock | None = None if check_same_thread else threading.Lock()

    @contextlib.contextmanager
    def _write(self: Self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that acquires the write lock (if set) and commits the transaction."""
        if self._write_lock is not None:
            with self._write_lock, self._conn:
                yield self._conn
        else:
            with self._conn:
                yield self._conn

    @property
    def db_path(self: Self) -> Path:
        """Read-only path to the underlying SQLite database file."""
        return self._db_path

    def apply_schema(self: Self) -> None:
        sql = _SCHEMA_PATH.read_text()
        with self._write() as conn:
            conn.executescript(sql)

    # --- Drafts ---

    def put_draft(self: Self, draft: Draft) -> None:
        intent_json: str | None = None
        if draft.intent_snapshot is not None:
            intent_json = draft.intent_snapshot.model_dump_json()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO drafts"
                "(draft_id, option_snapshot, fingerprint, created_at, expires_at, intent_snapshot)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    draft.draft_id,
                    draft.option_snapshot.model_dump_json(),
                    draft.fingerprint,
                    draft.created_at.isoformat(),
                    draft.expires_at.isoformat(),
                    intent_json,
                ),
            )

    def get_draft(self: Self, draft_id: str) -> Draft | None:
        row = self._conn.execute(
            "SELECT draft_id, option_snapshot, fingerprint, created_at, expires_at, intent_snapshot"
            " FROM drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            return None
        opt = Option.model_validate_json(row[1])
        intent: Intent | None = None
        if row[5] is not None:
            raw_intent = json.loads(row[5])
            kind = raw_intent.get("kind", "ride")
            if kind == "appointment":
                intent = AppointmentIntent.model_validate(raw_intent)
            else:
                intent = RideIntent.model_validate(raw_intent)
        return Draft(
            draft_id=row[0],
            option_snapshot=opt,
            fingerprint=row[2],
            created_at=datetime.fromisoformat(row[3]),
            expires_at=datetime.fromisoformat(row[4]),
            intent_snapshot=intent,
        )

    def consume_draft(self: Self, draft_id: str, at: datetime) -> bool:
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE drafts SET consumed_at = ? WHERE draft_id = ? AND consumed_at IS NULL",
                (at.isoformat(), draft_id),
            )
            return cur.rowcount == 1

    def get_draft_consumed_at(self: Self, draft_id: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT consumed_at FROM drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        dt = datetime.fromisoformat(row[0])
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    # --- Bookings ---

    def put_booking(self: Self, booking: Booking) -> None:
        now = datetime.now(UTC).isoformat()
        with self._write() as conn:
            conn.execute(
                """INSERT INTO bookings(booking_id, draft_id, provider, status, summary,
                                        created_at, updated_at, cancelled_at, cancellation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(booking_id) DO UPDATE SET
                       status = excluded.status,
                       summary = excluded.summary,
                       updated_at = excluded.updated_at""",
                (
                    booking.booking_id,
                    booking.draft_id,
                    booking.provider,
                    booking.status,
                    booking.summary.model_dump_json(),
                    now,
                    now,
                    None,
                    None,
                ),
            )

    def get_booking(self: Self, booking_id: str) -> Booking | None:
        row = self._conn.execute(
            "SELECT booking_id, draft_id, provider, status, summary"
            " FROM bookings WHERE booking_id = ?",
            (booking_id,),
        ).fetchone()
        if row is None:
            return None
        return Booking(
            booking_id=row[0],
            draft_id=row[1],
            provider=row[2],
            status=row[3],
            summary=Option.model_validate_json(row[4]),
        )

    def update_booking_status(
        self: Self, booking_id: str, status: str, event: BookingEvent
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE bookings SET status = ?, updated_at = ? WHERE booking_id = ?",
                (status, event.ts.isoformat(), booking_id),
            )
            conn.execute(
                "INSERT INTO booking_events(booking_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
                (
                    booking_id,
                    event.ts.isoformat(),
                    event.kind,
                    json.dumps(event.payload, default=str),
                ),
            )

    def list_active_bookings(self: Self) -> list[Booking]:
        rows = self._conn.execute(
            "SELECT booking_id FROM bookings"
            " WHERE status NOT IN ('completed','cancelled','failed')",
        ).fetchall()
        out: list[Booking] = []
        for (booking_id,) in rows:
            b = self.get_booking(booking_id)
            if b is not None:
                out.append(b)
        return out

    # --- Idempotency ---

    def claim_or_recall(self: Self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult:
        existing = self._conn.execute(
            "SELECT booking_id, response_blob FROM idempotency_keys WHERE key = ?",
            (key,),
        ).fetchone()
        if existing is not None:
            booking_id: str | None = existing[0]
            response_blob: str | None = existing[1]
            if booking_id is not None and response_blob is not None:
                booking = Booking.model_validate_json(response_blob)
                return ClaimResult(status="recalled", booking=booking)
            # Row exists but not yet finalised: a prior call claimed the key but has not
            # completed (process crash or concurrent retry). Return "in_progress" so the
            # caller can surface the race without issuing a duplicate provider call.
            return ClaimResult(status="in_progress", booking=None)
        with self._write() as conn:
            conn.execute(
                "INSERT INTO idempotency_keys(key, scope, booking_id, response_blob, created_at)"
                " VALUES (?, ?, NULL, NULL, ?)",
                (key, scope, datetime.now(UTC).isoformat()),
            )
        return ClaimResult(status="new", booking=None)

    def finalize_idempotency(self: Self, key: str, booking: Booking) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE idempotency_keys SET booking_id = ?, response_blob = ? WHERE key = ?",
                (booking.booking_id, booking.model_dump_json(), key),
            )

    # --- Pending confirmations (restart-survival) ---

    def put_pending_confirmation(
        self: Self,
        subject_id: str,
        session_id: str,
        request_json: str,
        expires_at: datetime,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_confirmations"
                "(subject_id, session_id, request_json, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    subject_id,
                    session_id,
                    request_json,
                    datetime.now(UTC).isoformat(),
                    expires_at.isoformat(),
                ),
            )

    def pop_pending_confirmation(self: Self, subject_id: str) -> ConfirmationRequest | None:
        row = self._conn.execute(
            "SELECT request_json FROM pending_confirmations WHERE subject_id = ?",
            (subject_id,),
        ).fetchone()
        if row is None:
            return None
        req = ConfirmationRequest.model_validate_json(row[0])
        with self._write() as conn:
            conn.execute(
                "DELETE FROM pending_confirmations WHERE subject_id = ?",
                (subject_id,),
            )
        return req

    def list_pending_confirmations(self: Self, now: datetime) -> list[PendingConfirmationRecord]:
        rows = self._conn.execute(
            "SELECT session_id, request_json FROM pending_confirmations WHERE expires_at > ?",
            (now.isoformat(),),
        ).fetchall()
        return [
            PendingConfirmationRecord(
                session_id=row[0],
                request=ConfirmationRequest.model_validate_json(row[1]),
            )
            for row in rows
        ]
