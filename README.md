# Lush Acres

An internet-facing Paper server for 4-10 players, reached through a
[playit.gg](https://playit.gg) tunnel. It has its own namespace, memory limit and
network policy, because it takes connections from the internet on a node shared
with everything else.

The server used to be called DripCraft. That name remains in this repository's name,
the Kubernetes namespace, the admin panel and the world folder, where players never see
it. Renaming the namespace means moving the live server and its volumes.

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
public internet and the server's game and voice ports. Neither can reach the LAN,
the tailnet or the rest of the cluster.

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

Voice chat needs a second tunnel, because voice is UDP and the Minecraft tunnel
carries TCP only. See [Voice chat](#voice-chat).

Both tunnels use playit Premium's Europe region, because the server is in Denmark, and
the Minecraft tunnel has a custom domain through a gateway. A region cannot be changed on
an existing tunnel, so moving to another region means a new tunnel and a new address. A
new tunnel takes a few minutes to start working, until the agent has picked it up.

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

## Voice chat

Simple Voice Chat is optional: players without the client mod join and play as
normal, they just cannot hear or talk. Players who want voice install a 2.6.x build
of the mod for Minecraft 26.3. A build from another 2.x series may not connect,
because the mod and the plugin must share a compatibility version.

In the playit dashboard, add a tunnel to this agent:

- **Tunnel type:** MC: Simple Voice Chat
- **Region:** Europe
- **Local address:** `10.96.25.65`, port `24454`
- **Proxy Protocol:** None

Put the address playit shows for that tunnel in `voice_host`, then restart the
server. It stays on the data volume rather than in Git, because this repository
is public. If playit ever changes the tunnel's address, change `voice_host` to match.

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- \
  sed -i 's/^voice_host=.*/voice_host=HOST:PORT/' /data/plugins/voicechat/voicechat-server.properties
```

The Minecraft connection working says nothing about voice, which is why voice has
its own check. The voice server answers a small UDP ping, with no game client needed,
so sending one to the tunnel's address proves the whole path. It does not prove that
audio works, which takes two players with the mod.

## Bedrock players

Geyser lets Bedrock players join, and Floodgate lets them do so without a Java account.
They use their own tunnel and address, port `19132`, with an up-to-date Bedrock client.
Their in-game names start with a dot, such as `.Steve`. Whitelist them with
`fwhitelist add GAMERTAG`.

In the playit dashboard, the tunnel is:

- **Tunnel type:** Minecraft Bedrock (NetherNet)
- **Region:** Europe
- **Local address:** `10.96.25.65`, port `19132`
- **Proxy Protocol:** None

playit marks this tunnel type as experimental, and says its ports and domains may change.
It cannot use a gateway, so it has a different address from the Java tunnel.

Geyser's settings are in `plugins/Geyser-Spigot/config.yml` on the data volume, not in
Git, because they include the tunnel's private token. Copy them from **Configuration help**
on the tunnel's page, and restart. The console then logs
`Registered with the external signaling service`.

`enforce-secure-profile` is off, because Bedrock players cannot chat while it is on.

## Grief log

Prism records who placed, broke or took what, in `plugins/prism/prism.db` on the data
volume, and it purges records older than six weeks. Players cannot use it. An operator
uses it in game:

- `/pr wand inspect`, then left-click a block or chest to see who changed it. A right
  click looks at the space next to the block instead. `/pr wand` puts the wand away.
- `/pr lookup p:NAME r:30 since:1d` lists what that player did within 30 blocks in
  the last day.
- `/pr rollback p:NAME r:30 since:1d` undoes it. `/pr undo` takes that back for blocks,
  and `/pr restore` applies the player's changes again.

Chests need care, because Prism can give back more than was taken:

- Never preview a rollback that includes chests. `/pr preview-rollback` puts the
  items in the chest for real, and `/pr preview-cancel` does not take them out. To
  preview safely, limit it to blocks with `a:block-break,block-place`.
- Rollback returns what was taken but does not remove what was put in. If a player
  picks up a stack of 16 and puts 11 back, a rollback adds 16 to the 11, leaving 27.
- `/pr undo` does not remove items a rollback returned.

So look in the chest before and after a rollback. Blocks, doors, broken chests with
their contents, TNT damage and lava all roll back exactly.

## Chest sorting

Chestsort+ lets players sort chests and their own inventory. It is off for each player
until they turn it on: `/chestsort` opens a dialog where they choose a sorting key
and whether to sort automatically. Players can use it without being operators because
`manifests/player-permissions.yml` grants `chestsort.use` to everyone. That file is in
Git and has its own name, so keep it that way: the admin panel treats any
`/data/permissions.yml` as a leftover from the test phase and offers to delete it.

The same dialog offers quick item transfer between a chest and the inventory, which
cannot be turned off for the whole server.

The original ChestSort crashes on start on Minecraft 26.x, because it cannot read
the new version format.

## The world

The server's files live in the `server` folder of the data volume, and the world in
the folder named by `LEVEL` inside it, with Minecraft's standard generation. Changing
`LEVEL` to a new name starts a fresh world there. The whitelist, operators and server
settings are outside the world folder, so they carry over. Inventories, advancements
and game rules are inside it, so they start over.

## Pre-generation

Chunky creates terrain ahead of time, so exploring does not make the server generate
it during play. Start it from **Pre-generate the world** on the admin panel's Dashboard.
The radius is in blocks and a radius of 0 skips that dimension. Nether distances count
eight times over in the overworld, so a Nether radius of one eighth the overworld's
covers the same ground. The work carries on across restarts. Check the seed first,
because a new seed means starting over.

It generates about 55 chunks a second here, so a radius of 10,000 in the overworld takes
about 8 hours. Each chunk takes about 18 KB, so expect that world to reach about 30 GB.
The periodic backups grow with it.

To control it, use the panel's Console page. `chunky pause` stops it and keeps the
progress, and `chunky continue` carries on. To stop for good, run `chunky cancel`
followed by `chunky confirm`, because cancelling asks for confirmation. Add a world, such
as `chunky pause minecraft:the_end`, to control one dimension. A restart resumes every
paused task, so pause them again afterwards.

Each dimension runs as its own task, and running one alone is the fastest. The overworld
ran at 57 to 70 chunks a second on its own, and about 37 alongside the other two.

## Profiling

Spark comes with Paper. Only operators can use it, and its replies do not come back
through `rcon-cli`. In game:

```text
/spark tps
/spark profiler start --timeout 600
```

Health reports and profiler results upload to a public link on spark.lucko.me unless
you add `--save-to-file`, which keeps them in `plugins/spark` on the data volume.

## Tuning

`manifests/tuning-patches.json` sets the Paper and Spigot options that have no
environment variable. The image applies it on every start, so a hand edit to one
of those options in the volume is overwritten. On a brand new volume the files do
not exist yet, so the patch is skipped with a warning and takes effect from the
second start.

Options in `paper-global.yml` go in `manifests/paper-global.yml` instead. The image
replaces that file with the short copy before it patches, so a patch finds nothing to
set and the server fails to start. That file sets 4 chunk workers, because Paper chooses
only 2 on this node, and players loading terrain share them with pre-generation.

It also turns on Paper's anti-xray, which hides ores from players until they are
next to them, with the Nether's ores added to the hidden list. Ores above Y 64 stay
visible, which is Paper's default. Honest players do not notice it.

Keep `mob-spawn-range` equal to `SIMULATION_DISTANCE`. Both limits apply, so the
smaller one wins: mobs cannot spawn where the world is not running.

The server shares its node with other workloads. Its 10 GiB of memory is reserved, and
it is guaranteed 3.4 of the node's 8 CPU cores, using more when the node is idle. That
guarantee covers CPU time only. The disk, the home network and the host machine are
still shared, and exclusive cores would need a change to the node's own configuration.

## Backups

The `backup` sidecar writes `/backups/world-*.tar.gz` every 6 hours and keeps 2 days.
It pauses any running pre-generation while it does, because tar reports a failed backup
if the world changes underneath it, and resumes only what it paused. Plugin and server
jars are left out, because the image downloads them again on start. These archives are
on the same disk as the world, and whether the nightly labmox VM backup covers this
volume has not been checked.

To restore, do not scale the StatefulSet down, because Argo CD scales it straight back
up. Instead, unpack the archive into a new folder and point the server at it:

1. From a temporary pod that mounts the `data-minecraft-0` and `backups-minecraft-0`
   claims, unpack the archive into a new folder on the data volume, such as
   `restored-20261106`. The pod must run as uid 1000, so the server can write there.
   Archives hold the contents of the server's folder, so unpack them inside the new
   folder rather than over the top of the volume.
2. In a commit, change `subPath: server` to the new folder in both containers. The
   server restarts into the restored files, and the image downloads the jars that
   periodic archives leave out.
3. The old folder stays on the volume until you remove it.

A periodic archive holds no secrets from Git, but it does hold Geyser's tunnel token and
Floodgate's key, so treat it as private. This procedure has not been run end to end.

The test server from before launch is kept twice, in case anyone wants it back. Its
files are still at the top of the data volume, outside the `server` folder, so this
server never reads them. A full copy, plugin jars included, is in
`/backups/pre-golive-20261106`. The sidecar prunes only the top level of `/backups`, so
that subfolder is never deleted automatically. Neither is on a separate disk.

To go back to the test server, revert the commit that started this server.

## Updating

Renovate proposes image digests. Bump `VERSION` and `PAPER_BUILD` by hand, after
a backup and only when plugins support the new version. Check `MODRINTH_PROJECTS`
at the same time. Minecraft worlds cannot be downgraded.

## Testing

```bash
python -m unittest discover -s tests
```

The tests need PyYAML and `kubectl`.
