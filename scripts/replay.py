"""Reconstruct a session transcript from logs. Usage: python scripts/replay.py <session_id>"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentic_cab.core.ids import parse_session_id
from agentic_cab.core.paths import session_chat_path, session_log_path
from agentic_cab.render.transcript import render


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: python scripts/replay.py <session_id>\n")
        return 2
    try:
        sid = parse_session_id(sys.argv[1])
    except ValueError:
        sys.stderr.write("invalid session_id: expected a 26-character uppercase ULID\n")
        return 2
    log_dir = Path("logs")
    log_path = session_log_path(log_dir, sid)
    chat_path = session_chat_path(log_dir, sid)
    if not log_path.exists():
        sys.stderr.write(f"no log at {log_path}\n")
        return 1
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    chat: list[dict[str, object]] = []
    if chat_path.exists():
        chat = [json.loads(line) for line in chat_path.read_text().splitlines() if line.strip()]
    md = render(scenario_name=f"replay:{sid}", seed=0, chat=chat, action_log=rows, invariants=[])
    sys.stdout.write(md + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
