#!/usr/bin/env bash
# scripts/uninstall.sh — remove the seven IP Filing Ecosystem agents from OpenClaw.
#
# Removes agents.entries.<id> for the seven ids (config is backed up first) and
# restarts the gateway. Workspaces and agent dirs are left on disk unless
# --purge is given.
#
# Usage:
#   scripts/uninstall.sh            unregister; keep ~/.openclaw/workspaces/ip-* on disk
#   scripts/uninstall.sh --purge    also delete workspaces and agent dirs
#
# Env:
#   OPENCLAW_BIN   path to the openclaw CLI (default: ~/.openclaw/bin/openclaw)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENCLAW="${OPENCLAW_BIN:-$HOME/.openclaw/bin/openclaw}"
OC_HOME="$HOME/.openclaw"
CONFIG="$OC_HOME/openclaw.json"
AGENT_IDS=(ip-orchestrator ip-disclosure ip-prior-art ip-embodiment ip-spec ip-claims ip-reviewer)

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "$OPENCLAW" ] || OPENCLAW="$(command -v openclaw || true)"
[ -n "$OPENCLAW" ] || fail "openclaw CLI not found (set OPENCLAW_BIN)"
[ -f "$CONFIG" ] || fail "config not found: $CONFIG"

step "1. Config backup"
ts="$(date +%Y%m%d-%H%M%S)"
cp "$CONFIG" "$CONFIG.bak.ipfe.$ts"
say "   $CONFIG -> $CONFIG.bak.ipfe.$ts"

step "2. Unregister agents"
removed=0
for id in "${AGENT_IDS[@]}"; do
  if "$OPENCLAW" config get "agents.entries.$id" >/dev/null 2>&1; then
    "$OPENCLAW" config unset "agents.entries.$id"
    say "   removed agents.entries.$id"
    removed=$((removed + 1))
  else
    say "   agents.entries.$id not present — skipped"
  fi
done

step "3. Validate config"
"$OPENCLAW" config validate

step "4. Workspaces"
if [ "$PURGE" = 1 ]; then
  for id in "${AGENT_IDS[@]}"; do
    for d in "$OC_HOME/workspaces/$id" "$OC_HOME/agents/$id"; do
      if [ -d "$d" ]; then rm -rf "$d"; say "   deleted $d"; fi
    done
  done
else
  say "   left on disk (use --purge to delete): $OC_HOME/workspaces/ip-* and $OC_HOME/agents/ip-*"
fi
rm -f "$REPO/openclaw/.rendered.patch.json5"

step "5. Gateway"
if [ "$removed" -gt 0 ]; then
  "$OPENCLAW" gateway restart
  ok=0
  for _ in $(seq 1 12); do
    if "$OPENCLAW" gateway health --timeout 5000 >/dev/null 2>&1; then ok=1; break; fi
    sleep 5
  done
  [ "$ok" = 1 ] || fail "gateway did not report healthy within 60 s"
  say "   gateway healthy"
else
  say "   nothing was removed — gateway not restarted"
fi

step "Done"
