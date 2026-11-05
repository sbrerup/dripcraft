# DripCraft

An internet-facing Paper server for 4-10 players, reached through a
[playit.gg](https://playit.gg) tunnel. It has its own namespace, memory limit and
network policy, because it takes connections from the internet on a node shared
with everything else.

Argo CD deploys `manifests/` from `main` to the labmox cluster, so anything merged
here goes live. The cluster finds this repository through
[`apps/dripcraft-public.yaml`](https://github.com/sbrerup/dripcloud/blob/main/apps/dripcraft-public.yaml)
in dripcloud.

The tunnel is outbound only: no router port forward, nothing exposed on the LAN
or the tailnet, and players never see the home IP address.

```text
player -> playit edge -> playit-agent pod -> minecraft pod (Paper + backup sidecar)
```

Network policy limits the server to HTTPS and DNS, and the agent to DNS, the
public internet and the server's game port. Neither can reach the LAN, the
tailnet or the rest of the cluster.

## Setup

These Secrets live only in the cluster, not in this public repository.

```bash
kubectl -n dripcraft-public create secret generic minecraft-rcon \
  --from-literal=password="$(openssl rand -hex 24)"
kubectl -n dripcraft-public create secret generic playit-agent \
  --from-file=secret-key=/path/to/key
```

In the playit dashboard, add one Minecraft Java tunnel to this agent:

- **Local address:** `10.96.25.65`, port `25565` (the fixed ClusterIP in
  `manifests/minecraft.yaml`)
- **Proxy Protocol:** Version 2

The Proxy Protocol setting must match `proxies.proxy-protocol` in
`manifests/paper-global.yml`. If only one side has it on, players are disconnected
immediately. It is also why a plain status check of port 25565 hangs; use
`mc-health`, which sends the header.

For the alert when the server goes down, create a check at
[healthchecks.io](https://healthchecks.io) with a 5 minute period and 5 minutes of
grace, then give the cluster its ping URL and the tunnel's public address. Until
this Secret exists, the `uptime-check` job does nothing.

```bash
kubectl -n dripcraft-public create secret generic uptime-check \
  --from-literal=host=TUNNEL_HOST --from-literal=port=TUNNEL_PORT \
  --from-literal=ping-url=PING_URL
```

## Operating

Run commands on the server with `rcon-cli`:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli whitelist add NAME
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli op NAME
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli list
```

The whitelist and operators are stored in the world volume rather than in Git,
because this repository is public.

Check tick times before changing settings. Under 50 ms keeps the server at full
speed. Spark's replies do not come back through `rcon-cli`, so use `mspt`:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli mspt
```

A restart announces itself in chat, then waits 60 seconds before stopping, so
players are not dropped without warning. Merging a change that restarts the server
therefore takes about a minute longer.

## Grief log

Prism records who placed, broke or took what, from the moment it is installed, in
`plugins/prism/prism.db` on the data volume, and it purges records older than six
weeks. Players never see it. An operator uses it in game:

- `/pr wand inspect`, then click a block to see who changed it. `/pr wand` puts the
  wand away.
- `/pr rollback p:NAME r:30 since:1d` undoes what that player did within 30 blocks
  in the last day.

## Starting the real world

The world lives in the folder named by `LEVEL` in `manifests/minecraft.yaml`.

1. In a pull request, change `LEVEL` to a new name, such as `dripcraft`, and merge
   it. The server restarts and creates a fresh world there. The whitelist, operators
   and server settings are outside the world folder, so they carry over. Inventories,
   advancements and game rules are inside it, so they start over.
2. Pre-generate the new world, so exploring does not make the server create terrain
   during play:

   ```bash
   scripts/launch.sh
   ```

   It returns straight away and the work carries on inside the server, also across
   restarts. The defaults (`OVERWORLD_RADIUS` 5000, `NETHER_RADIUS` and `END_RADIUS`
   2500) take about 3 hours at the rate measured here. Players can join while it runs.
   See how far it is with `rcon-cli chunky progress`.
3. Set any game rules again.
4. The old world stays on disk, and in the backups, until you delete its folder.

## Test night

Run through this with real players on the test world before the real start:

1. Add everyone to the whitelist, make one person an operator, and have everyone join.
2. Spread out, walking and flying in different directions, with some players in the
   Nether and the End.
3. Build a mob farm from YouTube and stand at it until it should be producing.
4. Have someone break a few blocks, find them with `/pr wand inspect` and undo them
   with `/pr rollback`.
5. While this goes on, check `mspt` with `rcon-cli`, and run
   `/spark profiler start --timeout 600` in game. Spark gives a link to the results.

## Tuning

`manifests/tuning-patches.json` sets the Paper and Spigot options that have no
environment variable. The image applies it on every start, so a hand edit to one
of those options in the volume is overwritten. On a brand new volume the files do
not exist yet, so the patch is skipped with a warning and takes effect from the
second start.

It also turns on Paper's anti-xray, which hides ores from players until they are
next to them, with the Nether's ores added to the hidden list. Ores above Y 64 stay
visible, which is Paper's default. Honest players do not notice it.

Keep `mob-spawn-range` equal to `SIMULATION_DISTANCE`. Both limits apply, so the
smaller one wins: mobs cannot spawn where the world is not running.

## Backups

The `backup` sidecar writes `/backups/world-*.tar.gz` every 6 hours and keeps 7
days. Plugin and server jars are left out, because the image downloads them
again on start. These archives are on the same disk as the world, and whether the
nightly labmox VM backup covers this volume has not been checked.

To restore, scale the StatefulSet to 0, unpack an archive over the `data` volume
from a temporary pod, and scale back up.

## Updating

Renovate proposes image digests. Bump `VERSION` and `PAPER_BUILD` by hand, after
a backup and only when plugins support the new version. Check `MODRINTH_PROJECTS`
at the same time. Minecraft worlds cannot be downgraded.

## Testing

```bash
python -m unittest discover -s tests
```

The tests need PyYAML and `kubectl`.
