#!/usr/bin/env bash
# scripts/install.sh — install the eight IP Filing Ecosystem agents into OpenClaw.
#
# Idempotent: re-running with nothing changed copies nothing and re-applies an
# identical patch. Never touches memory/ or any other file in a workspace, and
# never touches existing agents.entries.
#
# Usage:
#   scripts/install.sh              install / update
#   scripts/install.sh --check      report what would change; changes nothing
#   scripts/install.sh --no-restart install / update, but leave the gateway alone
#
# USE --no-restart WHEN A MATTER IS MID-RUN. `openclaw config patch` reports
# that new `agents.entries` are picked up without a restart; restarting the
# gateway would kill any in-flight agent call, and an ipfe run that loses a call
# resumes by re-issuing it — paying for the same 20-minute review twice, or
# worse, stopping at a half-written phase version. A restart is only needed for
# changes to an *existing* agent's model or tools, so after adding a reviewer
# you can install now and restart at the next quiet moment.
#
# Env:
#   OPENCLAW_BIN   path to the openclaw CLI (default: ~/.openclaw/bin/openclaw)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCLAW="${OPENCLAW_BIN:-$HOME/.openclaw/bin/openclaw}"
OC_HOME="$HOME/.openclaw"
CONFIG="$OC_HOME/openclaw.json"
PATCH_SRC="$REPO/openclaw/openclaw.patch.json5"
PATCH_RENDERED="$REPO/openclaw/.rendered.patch.json5"

AGENT_IDS=(patentcounsel ip-disclosure ip-prior-art ip-embodiment ip-spec ip-claims ip-reviewer ip-reviewer-b ip-searcher)
WORKSPACE_FILES=(SOUL.md AGENTS.md IDENTITY.md)
PER_FILE_CAP=20000     # OpenClaw bootstrapMaxChars default
WORKSPACE_CAP=60000    # per-workspace bootstrap total

CHECK=0
RESTART=1
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --no-restart) RESTART=0 ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
would() { if [ "$CHECK" = 1 ]; then printf '   would: %s\n' "$*"; else printf '   %s\n' "$*"; fi; }

# ---------------------------------------------------------------- 1. openclaw
step "1. OpenClaw CLI"
if [ -x "$OPENCLAW" ]; then
  say "   using $OPENCLAW ($("$OPENCLAW" --version 2>/dev/null | head -1 || echo 'version unknown'))"
elif command -v openclaw >/dev/null 2>&1; then
  OPENCLAW="$(command -v openclaw)"
  say "   using $OPENCLAW (from PATH)"
else
  if [ "$CHECK" = 1 ]; then
    say "   NOT FOUND: $OPENCLAW — set OPENCLAW_BIN. Gateway steps will be skipped in --check."
    OPENCLAW=""
  else
    fail "openclaw CLI not found at $OPENCLAW (set OPENCLAW_BIN)"
  fi
fi

# ------------------------------------------------------- 2. workspaces + caps
step "2. Workspaces (caps: ${PER_FILE_CAP} chars/file, ${WORKSPACE_CAP} chars/workspace)"
USER_SRC="$REPO/agents/_shared/USER.md"
[ -f "$USER_SRC" ] || fail "missing $USER_SRC"
CHANGES=0
for id in "${AGENT_IDS[@]}"; do
  src_dir="$REPO/agents/$id"
  ws="$OC_HOME/workspaces/$id"
  agent_dir="$OC_HOME/agents/$id/agent"
  total=0
  for f in "${WORKSPACE_FILES[@]}" USER.md; do
    if [ "$f" = USER.md ]; then src="$USER_SRC"; else src="$src_dir/$f"; fi
    [ -f "$src" ] || fail "missing $src"
    n=$(wc -c < "$src" | tr -d ' ')
    [ "$n" -le "$PER_FILE_CAP" ] || fail "$src is $n chars (> $PER_FILE_CAP)"
    total=$((total + n))
  done
  [ "$total" -le "$WORKSPACE_CAP" ] || fail "workspace $id totals $total chars (> $WORKSPACE_CAP)"
  say "   $id: $total chars across 4 files — within caps"

  if [ "$CHECK" = 0 ]; then
    mkdir -p "$ws" "$agent_dir"
  else
    [ -d "$ws" ] || would "mkdir -p $ws"
    [ -d "$agent_dir" ] || would "mkdir -p $agent_dir"
  fi

  for f in "${WORKSPACE_FILES[@]}" USER.md; do
    if [ "$f" = USER.md ]; then src="$USER_SRC"; else src="$src_dir/$f"; fi
    dst="$ws/$f"
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
      continue
    fi
    CHANGES=$((CHANGES + 1))
    if [ -f "$dst" ]; then would "update  $dst"; else would "create  $dst"; fi
    [ "$CHECK" = 0 ] && cp "$src" "$dst"
  done

  # Workspace skills (agents/<id>/skills/*/SKILL.md), copied recursively; other
  # files in the workspace skills dir are left alone.
  if [ -d "$src_dir/skills" ]; then
    while IFS= read -r -d '' src; do
      rel="${src#"$src_dir/skills/"}"
      dst="$ws/skills/$rel"
      if [ -f "$dst" ] && cmp -s "$src" "$dst"; then continue; fi
      CHANGES=$((CHANGES + 1))
      if [ -f "$dst" ]; then would "update  $dst"; else would "create  $dst"; fi
      if [ "$CHECK" = 0 ]; then mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"; fi
    done < <(find "$src_dir/skills" -type f -print0)
  fi
done
[ "$CHANGES" = 0 ] && say "   all workspace files already up to date"

# ------------------------------------------------------------- 3. backup
step "3. Config backup"
if [ "$CHECK" = 0 ]; then
  [ -f "$CONFIG" ] || fail "config not found: $CONFIG"
  ts="$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG" "$CONFIG.bak.ipfe.$ts"
  say "   $CONFIG -> $CONFIG.bak.ipfe.$ts"
else
  would "cp $CONFIG $CONFIG.bak.ipfe.<timestamp>"
fi

# ------------------------------------------------------------- 4. patch
step "4. Render and apply config patch"
[ -f "$PATCH_SRC" ] || fail "missing $PATCH_SRC"
home_esc="$(printf '%s' "$HOME" | sed 's/[&|\\]/\\&/g')"
sed "s|\${HOME}|$home_esc|g" "$PATCH_SRC" > "$PATCH_RENDERED"
say "   rendered \${HOME} -> $HOME into $PATCH_RENDERED"
if [ -n "$OPENCLAW" ]; then
  say "   dry-run:"
  "$OPENCLAW" config patch --file "$PATCH_RENDERED" --dry-run
  if [ "$CHECK" = 0 ]; then
    say "   applying:"
    "$OPENCLAW" config patch --file "$PATCH_RENDERED"
  else
    would "openclaw config patch --file $PATCH_RENDERED"
  fi
fi

# ------------------------------------------------------------- 5. validate
step "5. Validate config"
if [ -n "$OPENCLAW" ]; then
  "$OPENCLAW" config validate
fi

# ------------------------------------------------------------- 6. agents list
step "6. Registered agents"
check_agents() {
  local json="$1" missing=()
  if command -v python3 >/dev/null 2>&1; then
    missing=($(printf '%s' "$json" | python3 -c '
import json, sys
want = sys.argv[1:]
try:
    data = json.load(sys.stdin)
except Exception:
    print(" ".join(want)); sys.exit(0)
ids = {a.get("id") for a in data} if isinstance(data, list) else set()
print(" ".join(w for w in want if w not in ids))
' "${AGENT_IDS[@]}"))
  elif command -v jq >/dev/null 2>&1; then
    for id in "${AGENT_IDS[@]}"; do
      printf '%s' "$json" | jq -e --arg id "$id" 'map(.id) | index($id) != null' >/dev/null 2>&1 || missing+=("$id")
    done
  else
    for id in "${AGENT_IDS[@]}"; do
      printf '%s' "$json" | grep -q "\"id\": *\"$id\"" || missing+=("$id")
    done
  fi
  printf '%s\n' "${missing[@]:-}"
}
if [ -n "$OPENCLAW" ]; then
  agents_json="$("$OPENCLAW" agents list --json 2>/dev/null || echo '[]')"
  missing="$(check_agents "$agents_json" | tr '\n' ' ' | sed 's/ *$//')"
  if [ -z "$missing" ]; then
    say "   all ${#AGENT_IDS[@]} ids registered: ${AGENT_IDS[*]}"
  elif [ "$CHECK" = 1 ]; then
    say "   not yet registered (will be added by the patch): $missing"
  else
    fail "agents missing after patch: $missing"
  fi
fi

# ------------------------------------------------------------- 7. gateway
step "7. Gateway"
if [ "$RESTART" = 0 ]; then
  say "   --no-restart: gateway left running. New agents.entries apply without a"
  say "   restart; a change to an existing agent's model or tools does not, so"
  say "   run 'openclaw gateway restart' once no matter is mid-call."
elif [ "$CHECK" = 1 ]; then
  would "openclaw gateway restart; poll gateway health up to 60 s"
else
  "$OPENCLAW" gateway restart
  ok=0
  for _ in $(seq 1 12); do
    if "$OPENCLAW" gateway health --timeout 5000 >/dev/null 2>&1; then ok=1; break; fi
    sleep 5
  done
  [ "$ok" = 1 ] || fail "gateway did not report healthy within 60 s"
  say "   gateway healthy"
fi

# ------------------------------------------------------------- 8. python
step "8. Python toolchain"
if ! command -v uv >/dev/null 2>&1; then
  say "   uv not found — skipping 'uv sync' and 'ipfe check' (install uv to enable)"
elif [ "$CHECK" = 1 ]; then
  would "uv sync; uv run ipfe check (if that subcommand exists)"
else
  (cd "$REPO" && uv sync)
  if (cd "$REPO" && uv run ipfe --help 2>/dev/null | grep -q '\bcheck\b'); then
    (cd "$REPO" && uv run ipfe check)
  else
    say "   note: 'ipfe check' is not available yet (runner not installed or subcommand absent) — skipped"
  fi
fi

step "Done$([ "$CHECK" = 1 ] && echo ' (check mode — nothing was changed)')"
