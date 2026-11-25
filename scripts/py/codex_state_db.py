# Codex thread-state SQLite lookup (get_codex_session, Method 3).
# Invoked as: USED_CODEX_SESSION_IDS=... CODEX_PANE_TITLE=... python3 codex_state_db.py <codex_home> <cwd> <process_start_epoch>
import glob
import json
import math
import os
import sqlite3
import sys
from pathlib import Path

codex_home = sys.argv[1]
cwd = sys.argv[2]
start_raw = sys.argv[3].strip()
used = {sid for sid in os.environ.get("USED_CODEX_SESSION_IDS", "").split("\t") if sid}

# Find the newest state_*.sqlite by mtime.
dbs = sorted(
    glob.glob(os.path.join(codex_home, "state_*.sqlite")),
    key=os.path.getmtime,
    reverse=True,
)
if not dbs:
    sys.exit(0)

# Absolute epoch of the assistant process start (empty on platforms where it
# can't be determined, in which case matching falls back to most-recent).
try:
    process_start = float(start_raw) if start_raw else None
except ValueError:
    process_start = None

# Open read-only so we never conflict with a running codex writer.
try:
    db_uri = Path(os.path.abspath(dbs[0])).as_uri() + "?mode=ro"
    con = sqlite3.connect(db_uri, uri=True)
except sqlite3.Error:
    sys.exit(0)

try:
    cur = con.cursor()
    try:
        # Codex runs its own threads in the session's cwd: auto-review ("Guardian review", source {"subagent":...}) and
        # other subagents, and non-interactive `codex exec` runs. They are updated during the session, often after its
        # last user turn, and must never be restored in place of it.
        try:
            cur.execute(
                "SELECT id, updated_at FROM threads "
                "WHERE cwd = ? AND archived = 0 AND source NOT LIKE '%subagent%' AND source != 'exec' "
                "ORDER BY updated_at DESC",
                (cwd,),
            )
        except sqlite3.OperationalError:  # a schema without `source`
            cur.execute(
                "SELECT id, updated_at FROM threads "
                "WHERE cwd = ? AND archived = 0 "
                "ORDER BY updated_at DESC",
                (cwd,),
            )
        rows = cur.fetchall()
    except sqlite3.Error:
        # Codex versions its state DB when schemas change. An unknown or
        # partially migrated schema is a normal fallback condition, not a
        # reason to abort the parent save hook.
        rows = []
finally:
    con.close()


# Prefer threads whose last update happened after the process started
# (rules out stale threads in the same cwd). Fall back to most-recent
# overall if nothing qualifies — covers the edge case where a session
# was spawned but hasn't had any user turns yet.
def pick(rows, require_after_start):
    for sid, updated_at in rows:
        if not isinstance(sid, str) or not sid:
            continue
        if sid in used:
            continue
        try:
            updated_at_value = float(updated_at)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(updated_at_value):
            continue
        if (
            require_after_start
            and process_start is not None
            and updated_at_value < process_start
        ):
            continue
        return sid
    return None


# The Codex TUI titles its pane "<thread name> | <folder>", and session_index.jsonl maps names to thread ids. When several
# Codex panes share a cwd, that tells their threads apart; without a matching name the time-based pick applies.
def named(rows, title):
    name = title.rsplit(" | ", 1)[0].strip() if " | " in title else title.strip()
    if not name:
        return rows
    names = {}
    try:
        with open(os.path.join(codex_home, "session_index.jsonl"), encoding="utf-8") as index:
            for line in index:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("id"):
                    names[entry["id"]] = entry.get("thread_name") or ""
    except OSError:
        return rows
    return [row for row in rows if names.get(row[0]) == name] or rows


rows = named(rows, os.environ.get("CODEX_PANE_TITLE", ""))
sid = pick(rows, require_after_start=True) or pick(rows, require_after_start=False)
if sid:
    print(sid)
