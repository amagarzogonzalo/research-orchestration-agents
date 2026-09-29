#!/usr/bin/env bash
# Optional local compression proxy for tool output, logs and file dumps.
#
# Read .opencode/rules/context.md first. Three things to know before enabling:
#   1. Telemetry is ON BY DEFAULT in some of these tools. This script turns it off.
#   2. Some install extra MCP servers at USER scope, which leaks outside this
#      project until you unwrap. This script opts out.
#   3. Headline compression numbers are workload-dependent. Prose barely
#      compresses; repetitive JSON and logs compress enormously. Measure yours.
set -euo pipefail

PORT="$(grep -A5 '^\[compression\]' harness.toml | grep '^port' | tr -dc '0-9')"
PORT="${PORT:-8787}"

case "${1:-help}" in
  on)
    command -v headroom >/dev/null || {
      echo "not installed. See: https://github.com/headroomlabs-ai/headroom"
      echo "  uv tool install --python 3.13 'headroom-ai[all]'"
      exit 1; }
    export HEADROOM_BEACON=off DO_NOT_TRACK=1
    echo "starting compression proxy on :$PORT (telemetry off, no user-scope installs)"
    headroom wrap opencode --code-memory none
    ;;
  off)
    headroom unwrap opencode || true
    echo "unwrapped. Verify your opencode.json was restored: git diff opencode.json"
    ;;
  savings)
    headroom savings
    echo
    echo "If this is under ~15% on your traffic, the added moving part is not worth it."
    ;;
  *)
    cat <<'HELP'
make compress-on       start the proxy and route opencode through it
make compress-off      restore the original config
make compress-savings  measured reduction on YOUR traffic

Decide with the third one, not with anyone's README table. Compression pays off
on long sessions with heavy tool output; it does almost nothing on short
conversational work. And always `git diff opencode.json` after wrapping or
unwrapping — these tools edit your config in place.
HELP
    ;;
esac
