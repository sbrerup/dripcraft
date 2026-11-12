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
player -> playit edge -> playit-agent pod -> minecraft-creative pod (Paper + backup sidecar)
browser -> playit edge -> playit-agent pod -> web-map pod (Caddy) -> minecraft pod (web map)
```

Network policy limits both servers to HTTPS and DNS, and the agent to DNS, the
public internet, the servers' game and voice ports and the web map's proxy. None of
them can reach the LAN, the tailnet or the rest of the cluster, and the two servers
cannot reach each other.

Most of this page is about the survival server. The second one is described under
[Creative server](#creative-server).

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

## Creative server

A second, smaller Paper server with a flat world, where every player is in creative
mode. It is a separate server rather than a second world on the survival server, so
nothing made there can be carried into the survival world, and nobody teleports between
the two. Players join it at its own address, as a second entry in their server list.

It runs as the `minecraft-creative-0` pod from `manifests/creative.yaml`, with its own
volumes, whitelist, operators and backups. The world is one layer of grass on dirt and
bedrock, with no villages and no monsters. It runs the same Paper build as the survival
server, with Geyser, Floodgate and DiscordSRV only, and the same redstone setting, so a
contraption behaves the same on both. It has no voice chat, web map, grief log or
pre-generation.

Run commands on it the same way, with its pod's name:

```bash
kubectl -n dripcraft-public exec minecraft-creative-0 -c minecraft -- rcon-cli list
```

### Setting it up

It starts without any setup, using the `minecraft-rcon` Secret the survival server already
has, but nobody can join until it has a tunnel and a whitelist.

In the playit dashboard, add a second Minecraft Java tunnel to the same agent:

- **Region:** Europe
- **Local address:** `10.96.25.67`, port `25565` (the fixed ClusterIP in
  `manifests/creative.yaml`)
- **Proxy Protocol:** Version 2

Bedrock players need a tunnel of their own to this server too: a Minecraft Bedrock
(NetherNet) tunnel to `10.96.25.67`, port `19132`, set up as under
[Bedrock players](#bedrock-players). Each tunnel has its own token, which goes in this
server's `plugins/Geyser-Spigot/config.yml`.

The whitelist is this server's own. Add names one at a time as on the survival server, or
copy the survival server's whole whitelist across, Bedrock players included. Run it again
after adding someone to the survival server:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- cat /data/whitelist.json | \
  kubectl -n dripcraft-public exec -i minecraft-creative-0 -c minecraft -- \
  sh -c 'cat > /data/whitelist.json && rcon-cli whitelist reload'
```

### Chat between the two servers

Both servers run DiscordSRV. When each has its own bot and both are set to the same
Discord channel, what a player says on one server is posted to the channel by that
server's bot, and the other server shows it in game chat as a message from Discord. The
bot's name is what players see in front of those lines, so name each bot after its
server. This has not been tried yet, so check how it looks before telling players.

It must be a second bot, because a server ignores messages its own bot posted. Create
another application in the Discord developer portal, with the intents and channel
permissions listed under [Discord chat](#discord-chat), and give its token to this
server:

```bash
read -rs token && kubectl -n dripcraft-public create secret generic discord-bot-creative \
  --from-literal=token="$token"
kubectl -n dripcraft-public delete pod minecraft-creative-0
```

Then put the channel's ID under `Channels` in this server's
`plugins/DiscordSRV/config.yml` and restart it again. `manifests/creative-tuning-patches.json`
turns off the same four defaults as on the survival server.

Joins, leaves, deaths and advancements are posted to Discord by each server, and are not
expected to appear in the other server's game chat. Chat between the servers goes through
Discord, so it stops while Discord is down.

### Live status in Discord

This server can keep its own status message, as under
[Live status in Discord](#live-status-in-discord). It reads its webhook from a Secret
named `discord-status-creative`, and posts nothing until that exists. Give it the same
webhook URL as the survival server's to have both messages in one channel:

```bash
read -rs url && kubectl -n dripcraft-public create secret generic discord-status-creative \
  --from-literal=webhook-url="$url"
```

The container's log calls the Secret `discord-status`, because it shares the survival
server's script.

### Resources and backups

4 GiB of memory is reserved for it, with a 2 GiB heap. With nobody online it used
2.9 GiB in a local test. It reserves almost no CPU, on purpose: the node's CPU is almost
fully reserved, and firecrawl-api needs one core unreserved to roll out an update. So this
server runs on whatever is idle, and the survival server wins when both are busy.

Its `backup` sidecar writes an archive every 6 hours and keeps 7 days, on a 10 GiB volume
beside the 10 GiB data volume. The notes under [Backups](#backups) apply, except that this
server's files are at the top of its data volume, and its claims are
`data-minecraft-creative-0` and `backups-minecraft-creative-0`.

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

## Discord chat

DiscordSRV links Minecraft chat to one Discord channel, both ways. Joins, leaves, deaths
and advancements also go to the channel, and the bot says when the server starts or stops.
Typing `playerlist` in the channel lists who is online.

The bot token lives only in a Secret. A missing or wrong token turns the plugin off, and
the server starts as normal.

```bash
read -rs token && kubectl -n dripcraft-public create secret generic discord-bot \
  --from-literal=token="$token"
```

The bot needs the Server Members and Message Content intents in the Discord developer
portal, and View Channels, Send Messages, Embed Links and Read Message History in the
channel. The channel ID goes under `Channels` in `plugins/DiscordSRV/config.yml` on the
data volume, not in this public repository.

`manifests/tuning-patches.json` turns off four defaults: running server commands from
Discord with `!c`, which anyone with a role named Owner or Developer could otherwise do;
pinging the Discord server's owner when the game stalls; rewriting the channel topic; and
the placeholder `!ip` and `!site` answers. These apply from the second start after
installing, because the plugin creates its settings files on the first. Until a channel is
set, nothing is relayed, so the first start is safe.

## Live status in Discord

One message in a Discord channel shows whether the server is up and who is playing, and is
edited every minute. It is green with the players' names when the server answers, yellow
while it starts, and red while it stops or restarts. It ends with when it was last updated,
so a message that has stopped changing shows its age. That happens when the whole pod, the
node or the home connection is down.

It shows what the server sees, not the tunnel. During a playit outage it stays green with
the players still connected, while nobody new can join. Yellow also shows when the server
stops answering without stopping.

It names the players, which the public server list hides, so post it only where players can
see it.

The `discord-status` container beside the server runs `manifests/discord-status.sh`. It reads
the player list over RCON and edits the message through a Discord webhook. DiscordSRV ignores
webhook messages, so the message does not reach Minecraft chat.

To set it up, create a webhook in the channel (**Edit Channel**, **Integrations**,
**Webhooks**, **New Webhook**), give it a name and an icon, and copy its URL. Then put the
URL in a Secret:

```bash
read -rs url && kubectl -n dripcraft-public create secret generic discord-status \
  --from-literal=webhook-url="$url"
```

The container reads the Secret every minute, so this needs no restart. It posts the message,
then keeps editing it. Pin the message, or give it a channel of its own, so chat does not push
it out of view.

The message's ID is kept in `discord-status-message-id` in the server's folder, so after a
restart the same message is edited. If the message is deleted, a new one is posted. To move it
to another channel, replace the Secret with that channel's webhook and delete the old message:

```bash
read -rs url && kubectl -n dripcraft-public create secret generic discord-status \
  --from-literal=webhook-url="$url" --dry-run=client -o yaml | kubectl apply -f -
```

The container must keep running. If it stops, the pod becomes unready and the tunnel stops
sending players to the server, so the script never exits on an error from Discord or the
server. Check what it is doing with:

```bash
kubectl -n dripcraft-public logs minecraft-0 -c discord-status
```

## Tree felling

Timberella fells a whole tree when a player crouches and breaks one of its logs with a
stone axe or better. Without crouching, or with a wooden axe, a log breaks as normal.
Players can turn it off for themselves with `/timberella toggle`.

`manifests/tuning-patches.json` makes the axe wear by one point per log, where the
plugin's default is half that, and turns off its automatic replanting. These apply from
the second start after installing, because the plugin creates its settings file on the
first. To apply them sooner, edit `plugins/Timberella/config.yml` on the data volume, which
the plugin reloads within seconds.

It only fells logs with naturally grown leaves, so log cabins are safe, including walls
touching a tree. Prism records every log and leaf under the player's name, and a rollback
restores them. An axe with fewer than 10 durability left cuts one log at a time. On a
tree bigger than the axe's remaining durability, the whole tree still falls and the axe
stops at 1 instead of breaking. A very large mangrove can leave a log behind, because of
the plugin's size limits for each species.

Tree Feller was tried first and rejected. Prism did not record its felling by default, and
it took down same-wood walls touching a tree.

## Web map

squaremap draws the world from above as a map in the browser, at the game's own address
with `https://` in front. It shows the terrain and everything built on it, the spawn
point, the world border and where each player is, updated every second. Anyone who knows
the server's address can see every base and follow players around. A player is left off
the map while invisible, in spectator mode or wearing a carved pumpkin.

playit passes HTTPS through without ending it, so Caddy in the `web-map` pod gets the
certificate from Let's Encrypt itself and keeps it on its own small volume. The proxy has
its own pod, so changing it never restarts the server, and the server's map port, 8080,
takes connections from that pod only.

In the playit dashboard, the tunnel is:

- **Tunnel Type:** HTTPs (needs Premium)
- **Public Endpoint:** the gateway the Minecraft Java tunnel uses, under **Gateways**. The
  two tunnels share its domain, and the region comes from the gateway.
- **Agent:** this agent. While the list loads, it may say the gateway endpoint is not
  supported, and then it becomes selectable.
- **Origin Config:** Local IP `10.96.25.66` (the fixed ClusterIP in
  `manifests/web-map.yaml`), Http Port `8080`, Https Port `8443`
- **Proxy Protocol:** None

The origin fields start filled in with `127.0.0.1` and ports 80 and 443, as playit's
[HTTPS tunnel guide](https://playit.gg/support/https-tunnel/) expects. Here that is the
agent's own pod, where nothing listens, so replace them as above.

Then put the tunnel's domain in a Secret and restart the proxy so Caddy reads it. Until
the Secret exists, Caddy answers only for `localhost` and asks for no certificate.

```bash
kubectl -n dripcraft-public create secret generic web-map --from-literal=domain=MAP_DOMAIN
kubectl -n dripcraft-public delete pod -l app.kubernetes.io/name=web-map
```

Caddy asks for the certificate as soon as it starts, and Let's Encrypt checks the domain
through the tunnel, so create the tunnel first. Caddy keeps retrying if it fails. Its log
shows `certificate obtained successfully`:

```bash
kubectl -n dripcraft-public logs deploy/web-map
```

### Rendering

Nothing large renders on its own. squaremap draws chunks as they change, and the rest of
the pre-generated world only when an operator starts a render. Start with the overworld:

```bash
kubectl -n dripcraft-public exec minecraft-0 -c minecraft -- rcon-cli squaremap fullrender minecraft:overworld
```

`squaremap radiusrender minecraft:overworld 3000` renders 3,000 blocks around spawn
instead, and `X Z` after the radius moves the centre. Put `--` after `rcon-cli` when a
coordinate is negative, or `rcon-cli` reads it as an option. The other dimensions are
`minecraft:the_nether` and `minecraft:the_end`.

`manifests/tuning-patches.json` gives renders one thread, and one more for saving images,
where squaremap would take two of each here, and logs progress once a minute instead of
every second. These apply from the second start after installing, because the plugin
creates its settings file on the first. Before then, set the same values in
`plugins/squaremap/config.yml` on the data volume and run `squaremap reload`, which needs
no restart.

On one thread, the first renders took 9 minutes each for the overworld and the Nether,
about 1.6 million chunks each, and under 3 minutes for the mostly empty End. With two or
three players online, tick times went from about 14 ms to between 11 and 20 ms on
average, with single spikes up to 156 ms. Check `mspt` while a render runs, and pause it
if tick times climb:

- `squaremap pauserender minecraft:overworld` pauses, and the same command again carries on.
- `squaremap cancelrender minecraft:overworld` stops it.
- A render's progress is saved, so a restart resumes it, a paused one included. Pause it
  again afterwards.

Progress appears in the server log once a minute:

```bash
kubectl -n dripcraft-public logs minecraft-0 -c minecraft | grep squaremap | tail
```

The tiles are in `plugins/squaremap/web/tiles` on the data volume, as images at four zoom
levels: about 590 MB for all three dimensions. The backups leave them out, because a
render makes them again. To delete a
dimension's tiles, confirm the reset in the same RCON session, because each `rcon-cli`
command is a new one:

```bash
printf 'squaremap resetmap minecraft:overworld\nsquaremap confirm\n' | \
  kubectl -n dripcraft-public exec -i minecraft-0 -c minecraft -- rcon-cli
```

BlueMap was considered and rejected. Its 3D tiles would take about 25 GB for the
overworld alone, it renders every world as soon as it starts, and the image cannot patch
its settings files. Pl3xMap has only beta builds for 26.x, and Dynmap has none.

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
jars are left out, because the image downloads them again on start, and so are the web
map's tiles, because a render makes them again. These archives are
on the same disk as the world, and whether the nightly labmox VM backup covers this
volume has not been checked.

To restore, do not scale the StatefulSet down, because Argo CD scales it straight back
up. Instead, unpack the archive into a new folder and point the server at it:

1. From a temporary pod that mounts the `data-minecraft-0` and `backups-minecraft-0`
   claims, unpack the archive into a new folder on the data volume, such as
   `restored-20261106`. The pod must run as uid 1000, so the server can write there.
   Archives hold the contents of the server's folder, so unpack them inside the new
   folder rather than over the top of the volume.
2. In a commit, change `subPath: server` to the new folder in all three containers. The
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

The creative server uses the same image, Paper build and plugin builds, and the tests
fail if the two differ, so change both files in one commit. Both servers then restart.

## Testing

```bash
python -m unittest discover -s tests
```

The tests need PyYAML and `kubectl`.
