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
browser -> playit edge -> playit-agent pod -> web-map pod (Caddy, members' hub)
```

Network policy limits both servers to HTTPS and DNS, and the agent to DNS, the
public internet, the servers' game and voice ports and the web map's proxy. None of
them can reach the LAN, the tailnet or the rest of the cluster, and the two servers
cannot reach each other. The [archive](#the-archive) reaches the survival server's map
port and nothing else, not even the internet. One thing can connect to it: the ledger, an
application in a namespace of its own, which reads [the feed](#the-feed).

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
  sh -c 'cat > /tmp/whitelist.json && [ -s /tmp/whitelist.json ] &&
    cp /tmp/whitelist.json /data/whitelist.json && rcon-cli whitelist reload'
```

It answers `Reloaded the whitelist`. If the first command fails, nothing arrives and this
server's whitelist is left as it was. In Git Bash on Windows, run
`export MSYS_NO_PATHCONV=1` first: without it the `/data` path in the first command is
rewritten to a Windows path, and that command fails.

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
volume, and it purges records older than six weeks. The [archive](#the-archive) keeps a
copy of them for good. Players cannot use it. An operator uses it in game:

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

## The archive

The server notes far more about what players do than it keeps. The `archive` pod gathers
it into one SQLite database that only grows, `archive.db` on a volume of its own, so that
what happened can still be asked about later. Nothing in it is shown anywhere.

It is the ground for the ledger, an application of its own (`dripcraft-ledger`, in its own
repository and namespace) that keeps a record of what members did, for the hub and Discord
to be built on. The ledger gets Prism's rows from the archive through [the feed](#the-feed).

It keeps three things, as the sources gave them, with nothing worked out. A mistake in
reading them can then be put right later without anything having been lost.

- **Everything Prism writes.** [Prism](#grief-log) notes every block broken or placed,
  every item put into or taken from a chest, picked up, dropped or traded, every death,
  and more, each with who, where and when. It deletes them after six weeks. Every minute
  the archive copies the new rows into tables with Prism's own names and columns:
  `prism_activities` for what happened, and `prism_actions`, `prism_blocks`,
  `prism_items`, `prism_entity_types`, `prism_players`, `prism_worlds` and the rest for
  what its numbers stand for. The first time it runs, it copies everything Prism still has.
- **Where everyone is.** squaremap knows where each player on the map is, and keeps
  nothing. Every ten seconds the archive asks it, and writes a row to `positions` for each
  player: the world, the block they are at, which way they face, their health and armour.
  `watches` has the stretches of time during which it was asking, so a gap in someone's
  positions can be told from a gap in the watching. A player the map hides (invisible, in
  spectator mode or wearing a carved pumpkin) is not seen.
- **The game's counters over time.** The game counts what each player has done as totals,
  with no history. Each time it has saved a player's statistics file, which is about every
  five minutes while they play, the archive keeps the whole file in `totals`, compressed
  with zlib. The difference between two of them is what the player did in between.

Times are in seconds since 1970, in UTC. Players are known by UUID, with dashes, in all
three.

Every copied row has `source` in front of Prism's own columns. It is 0, and goes up by one
if Prism's database is ever replaced, because its numbering then starts again and means
other things. Rows of the old and the new database never pass for each other that way. A
newer Prism with more columns is followed without a change here.

Prism got about a million rows in the server's first week. A row takes about 100 bytes in
the archive, with its indexes, so that is about 100 MB a week and 5 GB a year. Positions
and counters add little to that. The volume is on the same disk as the world, which had
800 GB free when this was set up.

The server's logs, with every join, leave and death message, are not copied. The server
keeps those itself, in `logs`. The creative server is left out.

### Where it runs and what it can reach

It is `manifests/archive.py`, run by the `archive` pod from `manifests/archive.yaml`. It
has a pod of its own, so that changing the hub never interrupts it, and a fault in it never
takes the hub or the map off the tunnel.

- It mounts the survival server's folder read-only and opens Prism's database for reading
  only, so it cannot change the world or the grief log.
- Network policy lets it reach the server's map port, to ask who is on the map, and
  nothing else. It has no way to the internet.
- Network policy lets one thing connect to it: the ledger's pods, which read
  [the feed](#the-feed). Nothing else in the cluster can, and nothing from outside.

The archive says where every member was and what they did, in far more detail than the hub
shows. Treat it as private. Anything made from it for the hub should be decided on purpose,
because anyone who knows the server's address can read the hub. That holds for what the
ledger makes from the feed as well.

The volume is the `archive` claim, which Argo CD never removes. Like the world, it is not on
a separate disk, and the server's periodic backups do not include it.

### The feed

The archive answers one question, over HTTP on port 8080 of the `archive` Service: which
rows has Prism written after a given one? The ledger asks it every so often, and so follows
the grief log from a namespace of its own, where it cannot mount the server's files or this
database.

```text
GET /feed/activities?after=0:500000&until=1794171400&limit=1000
```

- `after` is the cursor from the answer before. Leave it out to start from the first row,
  which is also how everything is read again.
- `until` is a time, in seconds since 1970. The rows end before the first one that happened
  later, and no row is passed over to reach a later one. The ledger asks for rows up to a
  few minutes ago, and so gets every row, in the order Prism wrote them.
- `limit` is how many rows to give at the most. It is 1000 unless asked, and never more
  than 5000.

The answer is JSON with two things: `rows`, and `next`, the cursor to ask with next time.
When there are no rows to give, `next` is the cursor that was asked with.

Each row has `cursor`, `source`, `id` and `at`, which is when it happened, and then what
Prism noted: `action`, `world`, `x`, `y`, `z`, `block`, `blockData`, `replacedBlock`,
`item`, `quantity`, `entity`, `player`, `cause`, `causePlayer`, `causeEntity`,
`causeBlock` and `reversed`. Where Prism has a number, the row
has the name it stands for, such as `block-break`, `minecraft:oak_log` or a player's UUID
and name. What is empty is left out. `player` is who something happened to, as in a death,
and `causePlayer` is who did it.

A row is given only once the names for its numbers have been copied as well, which is at
the end of each minute's copying. Until then the feed ends at the row before it.

What the archive holds is private, so the feed is kept narrow:

- Only the ledger's pods can connect to it: pods named `dripcraft-ledger`, in the namespace
  `dripcraft-ledger`, on that one port. Nothing in this namespace can, and the tunnel's
  agent cannot be pointed at it.
- It gives out and takes nothing in. It understands `GET` and nothing else, and reads the
  archive through a connection of its own that cannot write.
- Nothing a member may have written is in any row: not what is on a sign, not the name of
  a renamed item or a name tag, and not what Prism keeps of the thing itself. Those are
  Prism's `descriptor`, `metadata` and `serialized_data`, and they stay in the archive.

Only Prism's rows are in the feed. Positions and the game's counters are not, yet.

The feed runs in the same pod as the gathering, beside it. If it cannot start, or cannot
answer, the gathering goes on and the pod's log says why. Rows are found by their number, a
page at a time, so an answer takes a few megabytes however much the archive holds.

### Looking into it

The pod logs once an hour how much it kept, and any source it cannot read:

```bash
kubectl -n dripcraft-public logs deploy/archive
```

To ask the archive something, open it for reading only. This counts what has happened, by
kind:

```bash
kubectl -n dripcraft-public exec deploy/archive -- python3 -c '
import sqlite3
db = sqlite3.connect("file:/archive/archive.db?mode=ro", uri=True)
for row in db.execute("SELECT action, count(*) FROM prism_activities JOIN prism_actions USING (source, action_id) GROUP BY action ORDER BY 2 DESC"):
    print(*row)'
```

A question asked this way runs inside the pod that does the gathering and shares its 256 MiB
of memory. Past that, the pod is stopped and started again, and a few seconds of positions
are lost. That happened once, on the day it was set up. So let SQLite do its sorting on
disk, which it does by itself in the pod's `/tmp`, and do not set `temp_store` to memory.
Read a big answer row by row rather than all at once.

To see what the feed gives, ask it from inside the pod, which is the one other place it can
be reached from:

```bash
kubectl -n dripcraft-public exec deploy/archive -- python3 -c '
import urllib.request
print(urllib.request.urlopen("http://localhost:8080/feed/activities?limit=3").read().decode())'
```

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

## Shared cure discounts

ShareCures lets a player share the discount they get for curing a zombie villager. It
is off for each player until they run `/sharecures on`; `/sharecures off` stops it and
`/sharecures` shows the current choice. A player who cures a villager without sharing
gets a one-line reminder that the command exists.

Prices stay vanilla. A cure gives the curer a permanent reputation on that villager,
which the game never passes on. When anyone opens a trade, the plugin lends them the
cure reputation of a sharing player on that villager, so they pay what the curer pays.
It works for villagers cured before the plugin was installed, and for players who were
offline at the time. A player who stops sharing takes the loan back, the next time each
other player opens a trade with that villager.

The choices are in `plugins/ShareCures/sharers.txt` on the data volume, one player UUID
per line. The loans are stored on each villager.

The plugin's source is in `sharecures/`. To release a new version, change `version` in
`sharecures/pom.xml`, merge, and push a tag named `sharecures-v` plus that version. The
`sharecures` workflow then publishes the jar as a GitHub release, and the release's
download URL goes in `PLUGINS` in both servers' manifests, before the Floodgate URL.
When `PAPER_BUILD` changes, change `paper.version` in `pom.xml` to match.

To build it without Java installed:

```bash
docker run --rm -v "$PWD/sharecures:/src" -w /src maven:3.9-eclipse-temurin-25 mvn -B package
```

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

It also sets `MaximumAttemptsForSystemDNSBeforeUsingFallbackDNS` to `-1`. By default the
plugin switches to public DNS servers after three failed lookups in a row and stays on them
until the server restarts. The network policy only lets the server reach the cluster's DNS,
so a short internet outage left the bot offline until the server was restarted. With `-1`
it keeps asking the cluster's DNS, and it retries the connection every 15 minutes at the
longest.

If chat stops crossing, look for `failed to resolve discord.com` in the server log.
Restarting the server brings the bot back.

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

squaremap draws the world from above as a map in the browser, at `/map/` on the game's
own address with `https://` in front. The address alone is the
[members' hub](#members-hub), which links to the map. It shows the terrain and everything built on it, the spawn
point, the world border and where each player is, updated every second. Anyone who knows
the server's address can see every base and follow players around. A player is left off
the map while invisible, in spectator mode or wearing a carved pumpkin.

playit passes HTTPS through without ending it, so Caddy in the `web-map` pod gets the
certificate from Let's Encrypt itself and keeps it on its own small volume. The proxy has
its own pod, so changing it never restarts the server, and the server's map port, 8080,
takes connections from that pod only.

Caddy sends `X-Robots-Tag: noindex, nofollow` with everything it serves, the hub and the map
alike, which asks search engines to list none of it. That keeps the site out of search
results and keeps nobody out of the site. The address itself is still public, because every
certificate Let's Encrypt issues is published in logs anyone can read.

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

## Members' hub

The game's own address with `https://` in front, the one the web map's tunnel serves,
opens the members' hub: a small site for the people who play here. For now it shows what
everyone has done in the survival world, and it is laid out so more can be added. Like the
map, anyone who knows the server's address can see it, countries included.

- **Home** (`/`) has everyone standing together on the meadow, each in their own skin, with
  their name, the flag of the country they last joined from and a green dot while they
  are playing. Someone who is playing waves. Below are who was here last, a few of the
  moments on the noticeboard, the feats earned lately, then what all of us have done
  together: the road to the first million blocks placed and broken, the server's days as
  a strip of bars (the latest four weeks, with the biggest day marked), what Lush Acres is
  built from, and what gets us, and last doors to the other pages.
- **Members** (`/members`) has a card for each member, in the order of their names, with
  whether they are playing or when they last played and one line about them: the part of
  the day they are mostly about, in the visitor's own time. `/members/NAME` is a member's
  own page. The member stands at the
  top, waves when the page opens or when touched, and turns when dragged. Below are time
  played, blocks placed and broken, mobs killed and villager trades, then the feats they
  hold, then when they mostly play (in the visitor's own time), what they build with,
  which mobs they have killed, what they get from villagers, their own bests, and the
  members they have built beside, and at the foot links to the members either side.
- **Moments** (`/moments`) has the server's biggest day, its busiest hour, the hour with
  most of us at work, and the deepest dig and highest build anyone has made, without
  names. `/records`, where this page used to be, opens it too.
- **Feats** (`/feats`) has every feat there is, how it is earned, and the members who hold
  it, whoever earned it first in front. A feat is something earned by what a member did,
  alone or with others, such as building in the same spot in the same hour. Nothing is
  given in the game for one, and none of them ranks members against each other.
- **Map** goes to the [web map](#web-map) at `/map/`.

Nothing on the hub ranks members against each other, and deaths are counted only for the
whole server, never for one member.

Moving between pages keeps the sky where it is while the land changes under it, slides
the page the way it lies from the last one, and lets a member's figure walk from where it
stood to where it stands on the next page. Going back returns to where the last page was
scrolled to. This uses the browser's view transitions, and a browser without them, or set
to reduce motion, changes page at once. With reduced motion nothing else moves either.

The hub is `manifests/hub.html`, `hub.css` and `hub.js`. The script draws
every page and changes the address without loading anything again, so there is no file
called `/members`. Caddy answers every address that is not a file, the map or the numbers
with `hub.html`, which is how a page can be reloaded or shared by its address. An address
that names no page shows "Nothing here".

The look is a view over the acres: a sky with a square sun, hills that fade into the
distance with trees and a cottage on them, and the meadow in front, where the members
stand, which fades into the page below. In dark mode it is night, with a moon, stars,
fireflies and light in the cottage's windows. `hub.js` draws the landscape out of blocks,
so there are no picture files, and the icon in the browser's tab is written out in
`hub.html`.

Members are drawn in three dimensions from their skins, the way the game builds a player:
six boxes, with a second layer for hats, jackets and sleeves. `hub.js` does this itself
with WebGL and uses no library. A browser without WebGL shows them flat, from the front.

The two fonts, Fraunces for headings and names and Figtree for text, are in `manifests/`
as `fraunces.woff2` and `figtree.woff2` and are served with the hub. Both come from
[Google Fonts' repository](https://github.com/google/fonts/tree/main/ofl) and are under
the SIL Open Font License 1.1, which allows serving and cutting them down. To keep them
small they hold only Latin letters and the weights from 400 to 800, and Fraunces only its
soft style at one optical size (`SOFT` 100, `WONK` 0, `opsz` 72). They were cut with
fontTools (`varLib.instancer`, then `subset` to WOFF2), which keeps the copyright and
licence notices inside the files.

Everything the hub serves is in the `player-stats` ConfigMap, which may hold 1 MiB. It is
about 185 kB now. The fonts count for a third more than their size, because a ConfigMap
keeps files that are not text as base64.

Time played comes from the game itself, which counts it in one file per player under
`players/stats` in the world folder, so it goes back to the world's first day. The
creative server is left out. The game saves a player's file when it saves the world and
when they leave, so the time for someone who is playing is a few minutes old. "Last
played" is when their file was last saved. Everything else on the hub comes from the
ledger (below).

The `player-stats` container beside Caddy in the `web-map` pod runs
`manifests/player-stats.sh`. Every five minutes it gathers the files into one, with names
from the whitelist and the server's name cache, and Caddy serves that with the hub. Of
everything the game counts for a player, only the time played is copied into it: the
other counters, a player's deaths among them, stay in the files on the server.
Only that container mounts the server's folder, read-only,
so Caddy never sees it, and changing the hub or the script restarts the proxy and not the
server.

The feats and the other numbers are not the game's. The ledger works them out from what
the [archive](#the-archive) has recorded, since 6 November 2026, and keeps them. In the
same round the container asks the ledger (`LEDGER_URL` in `web-map.yaml`) two things and
writes the answers beside the game's numbers:

- **`/data/feats.json`**: which feats there are and who holds them, when each was earned
  and with whom. The ledger also says in which part of the map, and the script leaves that
  out.
- **`/data/counted.json`**, from the ledger's `/stats`: blocks placed and broken, what
  everything is built from, what gets us, every day since recording began, the moments,
  and for each member blocks placed and broken, mobs killed and which, villager trades
  and what for, the blocks they build with, what they did in each hour of the day (in
  UTC), whom they have built beside, and their own bests. The script copies only these,
  each checked to be the kind of value it should be, with `uuid` for the ledger's
  `playerId` and the moments' hours as seconds. Anything else in the answer, such as where
  something happened, never reaches the page, and an answer that is not what the hub
  expects is treated as no answer.

Network policy lets the `web-map` pod reach the ledger's pod on one port, and the ledger
lets in that pod and nothing else, so a visitor's browser never reaches the ledger. The hub
does without it: when the ledger does not answer, the page keeps what it has, and a pod
that has just started shows only time played, when members last played, and the members
themselves until it does.

The hub loads nothing from other sites, so a visitor's browser tells nobody else what it
is looking at. The container fetches what it needs and Caddy serves the copies:

- **Skins** come from Mojang, once a day for each player. A Bedrock player has none there
  and gets a plain figure.
- **Countries** come from the addresses in the server's log, where each login is recorded.
  They are looked up in DB-IP's free country database, which the container downloads once
  a month, so no player's address is sent anywhere. The addresses and the database stay
  in a folder Caddy does not mount, and only the two-letter country reaches the page. The
  database's licence asks for the link to DB-IP at the foot of the page. A player on a
  VPN shows the VPN's country, and one whose address is IPv6 or unknown shows no flag.
- **Flags** are small pictures from flagcdn.com, fetched once for each country, because
  Windows has no flag emoji.

All of this is kept in temporary folders that go with the pod, so a restart of the proxy fetches
it again, and reads every log again for the addresses. Until a skin or a flag has been
fetched, the page shows a plain figure or the flag emoji.

Caddy serves the hub at the root of the address and passes `/map/` on to squaremap with
the folder's name taken off, which works because squaremap asks for its files by relative
address.

A player stays in the hub after leaving the whitelist, for as long as their file exists.
A Bedrock player who also plays on Java has two files and appears twice.

The container must keep running. If it stops, the pod becomes unready and the web map
goes off the tunnel, so the script never exits on a file it cannot read or a download
that fails. Check what it is doing with:

```bash
kubectl -n dripcraft-public logs deploy/web-map -c player-stats
```

Plan was considered and rejected. It is a plugin with its own web server and database,
and it only counts from the day it is installed.

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

Netherite left on the ground is gone after 30 seconds, where every other item stays
for 5 minutes. That includes netherite dropped on death. The list is every item whose
name begins with `netherite_`, and its times are in ticks, 20 to a second. The patch
sets the whole list, because Paper's own list has cobblestone in it at 15 seconds.

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

The tests need PyYAML and `kubectl`. The ones that run `manifests/player-stats.sh` also
need `sh` and `jq`, and those that ask a stand-in for the ledger `curl` as well. They are
skipped without them. The ones that run `manifests/archive.py`
need nothing more than Python. One of them starts it for a few seconds and asks its feed on
a port of the machine the tests run on.
