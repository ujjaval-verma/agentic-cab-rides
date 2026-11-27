"""Reconstruct a session transcript from logs. Usage: python scripts/replay.py <session_id>

Reads `<log dir>/<sid>.jsonl` and `<chat dir>/<sid>.chat.jsonl`; both dirs come from
AGENTIC_CAB_LOG_DIR / AGENTIC_CAB_CHAT_DIR (defaults: logs, the log dir).
"""

from __future__ import annotations

import json
import os
import sys

from agentic_cab.core.ids import parse_session_id
from agentic_cab.core.paths import session_chat_path, session_log_path
from agentic_cab.core.settings import RuntimeSettings
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
    settings = RuntimeSettings.from_env(os.environ)
    log_path = session_log_path(settings.log_dir, sid)
    chat_path = session_chat_path(settings.chat_dir, sid)
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
