#!/usr/bin/env bash
# Pre-generates a fresh world in the background, in all three dimensions. Run it
# once the LEVEL change has gone live (see README). It returns straight away, and
# the work carries on inside the server, including across restarts.
#
# Usage: scripts/launch.sh [kubectl-context]
set -euo pipefail

OVERWORLD_RADIUS="${OVERWORLD_RADIUS:-5000}"
NETHER_RADIUS="${NETHER_RADIUS:-2500}"
END_RADIUS="${END_RADIUS:-2500}"

kube=(kubectl -n dripcraft-public)
if [[ $# -gt 0 ]]; then
  kube+=(--context "$1")
fi

rcon() {
  "${kube[@]}" exec minecraft-0 -c minecraft -- rcon-cli "$@"
}

if ! rcon chunky progress | grep -q 'No tasks running'; then
  echo "Chunky is already generating. See it with: rcon-cli chunky progress" >&2
  exit 1
fi

# Saved tasks are kept per dimension name, so ones from an earlier world would
# otherwise make Chunky ask for confirmation.
rcon chunky cancel

pregenerate() {
  rcon chunky world "$1"
  rcon chunky center 0 0
  rcon chunky radius "$2"
  rcon chunky start
}

pregenerate minecraft:overworld "$OVERWORLD_RADIUS"
pregenerate minecraft:the_nether "$NETHER_RADIUS"
pregenerate minecraft:the_end "$END_RADIUS"

rcon chunky progress
