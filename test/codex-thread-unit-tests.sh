#!/usr/bin/env bash
# Hermetic unit tests for the Codex thread-state lookup (scripts/py/codex_state_db.py): Codex's own subagent and exec
# threads in the session's cwd are never picked, and the pane title tells sessions sharing a cwd apart.
# Run locally with:  bash test/codex-thread-unit-tests.sh   (or: just test-codex-threads)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT INT TERM

PASS=0
FAIL=0
assert_eq() {
	local desc="$1" expected="$2" actual="$3"
	if [ "$expected" = "$actual" ]; then
		PASS=$((PASS + 1))
		printf '  [pass] %s\n' "$desc"
	else
		FAIL=$((FAIL + 1))
		printf '  [FAIL] %s\n        expected: [%s]\n        actual:   [%s]\n' "$desc" "$expected" "$actual"
	fi
}

python3 - "$SANDBOX/state_5.sqlite" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE threads (id TEXT, cwd TEXT, archived INTEGER, source TEXT, updated_at INTEGER)")
db.executemany("INSERT INTO threads VALUES (?, '/w', 0, ?, ?)", [
    ("main-old", "cli", 2000),
    ("main-new", "vscode", 3000),
    ("guardian", '{"subagent":{"other":"guardian"}}', 3500),
    ("exec-run", "exec", 3600),
])
db.commit()
PY
printf '%s\n' '{"id":"main-old","thread_name":"Fix the login flow"}' '{"id":"main-new","thread_name":"Write the report"}' >"$SANDBOX/session_index.jsonl"
pick() { CODEX_PANE_TITLE="${1:-}" USED_CODEX_SESSION_IDS="${2:-}" python3 "$REPO_DIR/scripts/py/codex_state_db.py" "$SANDBOX" /w 1000; }

echo "== codex_state_db.py =="
assert_eq "auto-review and exec threads are skipped" "main-new" "$(pick)"
assert_eq "the pane title names the thread"          "main-old" "$(pick 'Fix the login flow | w')"
assert_eq "an unknown title falls back to time"      "main-new" "$(pick 'Something else | w')"
assert_eq "a thread used by another pane is skipped" "main-old" "$(pick '' main-new)"

python3 - "$SANDBOX/old/state_5.sqlite" <<'PY'
import os, sqlite3, sys
os.makedirs(os.path.dirname(sys.argv[1]))
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE threads (id TEXT, cwd TEXT, archived INTEGER, updated_at INTEGER)")
db.execute("INSERT INTO threads VALUES ('only', '/w', 0, 3000)")
db.commit()
PY
assert_eq "a schema without a source column still works" "only" "$(python3 "$REPO_DIR/scripts/py/codex_state_db.py" "$SANDBOX/old" /w 1000)"

echo
echo "codex thread unit tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
