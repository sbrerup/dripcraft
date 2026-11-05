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

Two Secrets live only in the cluster, not in this public repository.

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

## Operating

Run commands on the server with `rcon-cli`:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli whitelist add NAME
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli op NAME
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli list
```

The whitelist and operators are stored in the world volume rather than in Git,
because this repository is public.

Pre-generate the world once, so exploring does not stall the main thread. This
is safe to run with players online, and `chunky cancel` stops it:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli chunky radius 3000
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli chunky start
```

Check tick times before changing settings. Under 50 ms keeps the server at full
speed. Spark's replies do not come back through `rcon-cli`, so use `mspt`:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli mspt
```

## Tuning

`manifests/tuning-patches.json` sets the Paper and Spigot options that have no
environment variable. The image applies it on every start, so a hand edit to one
of those options in the volume is overwritten. On a brand new volume the files do
not exist yet, so the patch is skipped with a warning and takes effect from the
second start.

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
