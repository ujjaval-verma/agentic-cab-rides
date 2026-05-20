"""SqliteStateStore — see spec §10a + CLAUDE.md inv. 5, 7, 11."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from agentic_cab.core.ports import ClaimResult
from agentic_cab.core.types import Booking, BookingEvent, Draft, Option

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SqliteStateStore:
    def __init__(self: Self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))  # default isolation (deferred)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    @property
    def db_path(self: Self) -> Path:
        """Read-only path to the underlying SQLite database file."""
        return self._db_path

    def apply_schema(self: Self) -> None:
        sql = _SCHEMA_PATH.read_text()
        with self._conn:
            self._conn.executescript(sql)

    # --- Drafts ---

    def put_draft(self: Self, draft: Draft) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO drafts(draft_id, option_snapshot, fingerprint, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    draft.draft_id,
                    draft.option_snapshot.model_dump_json(),
                    draft.fingerprint,
                    draft.created_at.isoformat(),
                    draft.expires_at.isoformat(),
                ),
            )

    def get_draft(self: Self, draft_id: str) -> Draft | None:
        row = self._conn.execute(
            "SELECT draft_id, option_snapshot, fingerprint, created_at, expires_at"
            " FROM drafts WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            return None
        opt = Option.model_validate_json(row[1])
        return Draft(
            draft_id=row[0],
            option_snapshot=opt,
            fingerprint=row[2],
            created_at=datetime.fromisoformat(row[3]),
            expires_at=datetime.fromisoformat(row[4]),
        )

    def consume_draft(self: Self, draft_id: str, at: datetime) -> bool:
        with self._conn:
            cur = self._conn.execute(
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
        return datetime.fromisoformat(row[0])

    # --- Bookings ---

    def put_booking(self: Self, booking: Booking) -> None:
        now = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
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
        with self._conn:
            self._conn.execute(
                "UPDATE bookings SET status = ?, updated_at = ? WHERE booking_id = ?",
                (status, event.ts.isoformat(), booking_id),
            )
            self._conn.execute(
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
            # In-progress: previous claim wasn't finalised. Caller may retry the provider
            # call with the same key. P3 restart-survival hardens this with intent-bound
            # keys and a proper "in_progress" status — tracked in FOLLOWUPS.md.
            return ClaimResult(status="new", booking=None)
        with self._conn:
            self._conn.execute(
                "INSERT INTO idempotency_keys(key, scope, booking_id, response_blob, created_at)"
                " VALUES (?, ?, NULL, NULL, ?)",
                (key, scope, datetime.now(UTC).isoformat()),
            )
        return ClaimResult(status="new", booking=None)

    def finalize_idempotency(self: Self, key: str, booking: Booking) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE idempotency_keys SET booking_id = ?, response_blob = ? WHERE key = ?",
                (booking.booking_id, booking.model_dump_json(), key),
            )
