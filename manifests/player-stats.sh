#!/bin/sh
# Writes the numbers for the members' hub (see README). It runs beside
# Caddy in the web-map pod and reads the survival world's own statistics files,
# which the game keeps for every player, so the server needs no plugin for this.
#
# Every few minutes it gathers them into one file that Caddy serves. The game saves
# a player's statistics when it saves the world and when they leave, so the numbers
# for someone who is playing are a few minutes old.
#
# It also fetches each player's skin from Mojang and each country's flag from
# flagcdn.com, and works out the country each player last joined from. Addresses come from the server's log and are looked up in
# a database downloaded from DB-IP, so no address is ever sent anywhere. Addresses
# and the database stay in $state, which Caddy does not serve. Only the country
# reaches the page.
#
# And it asks the ledger for the feats members have earned and for what it has
# counted from the server's log, such as blocks placed and broken, and writes both
# beside the numbers.
#
# The server's folder is mounted read-only. This must keep running whatever is in
# it, and whatever Mojang, DB-IP or the ledger answer. A container that stops makes
# the pod unready, which takes the web map off the tunnel.

server=${SERVER_DIR:-/server}
world=${WORLD:-dripcraft}
out=${OUT_FILE:-/out/stats.json}
state=${STATE_DIR:-/state}
interval=${INTERVAL:-300}
# 0 turns off everything that uses the internet, for the tests.
fetch=${FETCH:-1}
skins=$(dirname "$out")/skins
flags=$(dirname "$out")/flags
feats=$(dirname "$out")/feats.json
counted=$(dirname "$out")/counted.json
# Where the ledger answers, without a slash at the end. Empty leaves the feats and
# what the ledger counts out.
ledger=${LEDGER_URL:-}
ledger=${ledger%/}

# Logs a line only when it differs from the last one, so a problem that lasts is
# logged once rather than every round.
last_log=
say() {
  [ "$1" = "$last_log" ] || echo "$1"
  last_log=$1
}

# True when a file is missing or was last changed more than $2 seconds ago.
older_than() {
  [ ! -e "$1" ] || [ $(($(date +%s) - $(stat -c %Y "$1"))) -gt "$2" ]
}

# Downloads DB-IP's free country database, which comes out monthly. Before a new
# month's file is published, last month's is the newest. A failed download is tried
# again an hour later, and the old database stays in use until then.
update_database() {
  [ "$fetch" = 1 ] || return 0
  older_than "$state/dbip.csv.gz" 2592000 || return 0
  older_than "$state/dbip.tried" 3600 || return 0
  : > "$state/dbip.tried"
  year=$(date +%Y)
  month=$(date +%m)
  month=${month#0}
  if [ "$month" = 1 ]; then last=$((year - 1))-12; else last=$year-$(printf %02d $((month - 1))); fi
  for name in "$year-$(printf %02d "$month")" "$last"; do
    if curl -fsS --max-time 120 --max-filesize 50000000 -o "$state/dbip.tmp" \
        "https://download.db-ip.com/free/dbip-country-lite-$name.csv.gz" 2>/dev/null &&
        gzip -t "$state/dbip.tmp" 2>/dev/null; then
      mv "$state/dbip.tmp" "$state/dbip.csv.gz"
      # Look every address up again in the new database.
      : > "$state/countries"
      echo "Downloaded the country database for $name."
      return 0
    fi
  done
  rm -f "$state/dbip.tmp"
  echo "Could not download the country database. Trying again in an hour."
}

# Keeps the address each player last joined from in $state/addresses, as lines of
# a name and an address. The first round reads every log, and later rounds only the
# current one. The pattern starts at the start of the line and allows only a name
# before the address, so nothing typed in chat can pass for a login.
all_logs=1
update_addresses() {
  touch "$state/addresses"
  if {
        [ -z "$all_logs" ] || zcat "$server"/logs/*.log.gz 2>/dev/null
        cat "$server/logs/latest.log" 2>/dev/null
      } |
      sed -n 's/^\[[0-9:]*\] \[Server thread\/INFO\]: \([A-Za-z0-9_.]*\)\[\/\([0-9]*\.[0-9]*\.[0-9]*\.[0-9]*\):[0-9]*\] logged in with entity id .*/\1 \2/p' |
      cat "$state/addresses" - | awk '{ last[$1] = $2 } END { for (name in last) print name, last[name] }' \
      > "$state/addresses.tmp"; then
    mv "$state/addresses.tmp" "$state/addresses"
    all_logs=
  fi
}

# Looks up addresses that have no country yet, all in one pass over the database,
# and keeps the answers in $state/countries as lines of an address and a country
# code. An address the database does not know gets "-", so it is not looked up again.
update_countries() {
  touch "$state/countries"
  [ -s "$state/dbip.csv.gz" ] || return 0
  missing=$(awk 'NR == FNR { known[$1] = 1; next } !($2 in known) { print $2 }' "$state/countries" "$state/addresses" | sort -u | tr '\n' ' ')
  # With an empty first file, awk takes the second for the first and prints nothing.
  [ -s "$state/countries" ] || missing=$(awk '{ print $2 }' "$state/addresses" | sort -u | tr '\n' ' ')
  [ -n "$missing" ] || return 0
  zcat "$state/dbip.csv.gz" 2>/dev/null | awk -F, -v want="$missing" '
    function number(address,  part) {
      split(address, part, ".")
      return ((part[1] * 256 + part[2]) * 256 + part[3]) * 256 + part[4]
    }
    BEGIN { count = split(want, wanted, " "); for (i = 1; i <= count; i++) value[i] = number(wanted[i]) }
    index($1, ":") { next }
    {
      from = number($1); to = number($2)
      for (i = 1; i <= count; i++) if (value[i] >= from && value[i] <= to) found[i] = $3
    }
    END { for (i = 1; i <= count; i++) print wanted[i], (found[i] ~ /^[A-Z][A-Z]$/ && found[i] != "ZZ" ? found[i] : "-") }
  ' >> "$state/countries"
}

# Fetches one player's skin from Mojang into $skins, and notes whether it is for the
# slim or the wide model. A skin is fetched again after a day, and a failed fetch is
# tried again after an hour.
update_skin() {
  uuid=$1
  if [ -e "$skins/$uuid.png" ]; then wait=86400; else wait=3600; fi
  older_than "$state/skin-$uuid" "$wait" || return 0
  [ -s "$state/skin-$uuid" ] || : > "$state/skin-$uuid"
  touch "$state/skin-$uuid"
  profile=$(curl -fsS --max-time 10 "https://sessionserver.mojang.com/session/minecraft/profile/$(printf %s "$uuid" | tr -d -)" 2>/dev/null) || return 0
  skin=$(printf %s "$profile" | jq -r '
    .properties[]? | select(.name == "textures") | .value | @base64d | fromjson | .textures.SKIN
    | select(.url) | "\(.url | sub("^http:"; "https:")) \(.metadata.model // "wide")"' 2>/dev/null)
  url=${skin% *}
  model=${skin#* }
  case $url in
    https://textures.minecraft.net/texture/*) ;;
    *) return 0 ;;
  esac
  curl -fsS --max-time 15 --max-filesize 200000 -o "$skins/$uuid.tmp" "$url" 2>/dev/null &&
    [ "$(head -c 4 "$skins/$uuid.tmp" | tail -c 3)" = PNG ] &&
    mv "$skins/$uuid.tmp" "$skins/$uuid.png" &&
    printf '%s\n' "$model" > "$state/skin-$uuid"
  rm -f "$skins/$uuid.tmp"
}

# Fetches a small picture of a country's flag into $flags, once. Windows has no flag
# emoji, so without the picture most players would see two letters. A failed fetch is
# tried again next round, and the page shows the emoji until then.
update_flag() {
  code=$(printf %s "$1" | tr A-Z a-z)
  [ ! -e "$flags/$code.png" ] || return 0
  curl -fsS --max-time 10 --max-filesize 50000 -o "$flags/$code.tmp" "https://flagcdn.com/w40/$code.png" 2>/dev/null &&
    [ "$(head -c 4 "$flags/$code.tmp" | tail -c 3)" = PNG ] &&
    mv "$flags/$code.tmp" "$flags/$code.png"
  rm -f "$flags/$code.tmp"
}

# Asks the ledger which feats there are and who holds them, and writes what the hub
# shows of that beside the numbers: each member's feats, when each was earned and
# with whom. The ledger also says where, and that is left out here, so it never
# reaches the page. The ledger is a separate application (see README), and the hub
# does without it: when it does not answer, the page keeps the feats it has, and
# with no address set there are none.
feats_log=
update_feats() {
  [ -n "$ledger" ] || return 0
  if curl -fsS --max-time 10 --max-filesize 1000000 -o "$state/feats-all.tmp" "$ledger/feats" 2>/dev/null &&
      curl -fsS --max-time 10 --max-filesize 5000000 -o "$state/feats-held.tmp" "$ledger/feats/earned" 2>/dev/null &&
      jq -cn --slurpfile all "$state/feats-all.tmp" --slurpfile held "$state/feats-held.tmp" --argjson now "$(date +%s)" '
        # "2026-11-13T22:53:24+00:00", with or without parts of a second, as seconds.
        def seconds: sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z") | fromdateiso8601;
        {
          updated: $now,
          feats: [$all[0][] | {key, name, earned, repeatable}],
          members: [
            $held[0][] | {
              uuid: .playerId,
              earned: [
                .earned[] | {
                  feat,
                  at: (.happenedAt | seconds),
                  with: (.together // []),
                  number,
                  day: (if .feat == "day-to-remember" then .occasion else null end)
                }
              ]
            }
          ]
        } | select((.feats | length) > 0)' > "$feats.tmp" 2>/dev/null &&
      [ -s "$feats.tmp" ] && mv "$feats.tmp" "$feats"; then
    line="The feats are up to date."
  else
    line="Could not ask the ledger for the feats, so the page keeps the ones it has."
  fi
  rm -f "$feats.tmp" "$state/feats-all.tmp" "$state/feats-held.tmp"
  # Logged only when it changes, and apart from the line about the numbers, so
  # the two do not take turns in the log every round.
  [ "$line" = "$feats_log" ] || echo "$line"
  feats_log=$line
}

# Asks the ledger what it has counted from the server's log, for the whole server and
# for each member: blocks placed and broken, what everything is built from, what gets
# us, every day, a few moments, and each member's mobs, trades, hours and own bests.
# Only the parts the hub shows are copied, each checked to be what it should be, so
# nothing else the ledger may know, such as where something happened, can reach the
# page. An answer that is not what the hub expects counts as no answer: the page
# keeps what it has, and with no address set there is nothing.
counted_log=
update_counted() {
  [ -n "$ledger" ] || return 0
  if curl -fsS --max-time 10 --max-filesize 5000000 -o "$state/counted.tmp" "$ledger/stats" 2>/dev/null &&
      jq -c --argjson now "$(date +%s)" '
        def seconds: sub("\\.[0-9]+"; "") | sub("\\+00:00$"; "Z") | fromdateiso8601;
        def number: if type == "number" then . else error("not a number") end;
        def text: if type == "string" then . else error("not text") end;
        def day: text | if test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$") then . else error("not a day") end;
        def maybe(f): if . == null then null else f end;
        # A list may be left out when it is empty.
        def many(f): (. // []) | if type == "array" then map(f) else error("not a list") end;
        def counts: many({name: (.name | text), count: (.count | number)});
        {
          updated: $now,
          placed: (.placed | number),
          broken: (.broken | number),
          builtFrom: {kinds: (.builtFrom.kinds | number), most: (.builtFrom.most | counts)},
          whatGetsUs: (.whatGetsUs | counts),
          days: (.days | many({day: (.day | day), placed: (.placed | number), broken: (.broken | number), members: (.members | number)})),
          moments: {
            biggestDay: (.moments.biggestDay | maybe({day: (.day | day), blocks: (.blocks | number)})),
            busiestHour: (.moments.busiestHour | maybe({at: (.at | text | seconds), blocks: (.blocks | number)})),
            fullestHour: (.moments.fullestHour | maybe({at: (.at | text | seconds), members: (.members | number)}))
          },
          members: (.members | many({
            uuid: (.playerId | text),
            placed: (.placed | number),
            broken: (.broken | number),
            mobsKilled: (.mobsKilled | number),
            mobs: (.mobs | counts),
            trades: (.trades | number),
            gets: (.gets | counts),
            kinds: (.kinds | number),
            buildsWith: (.buildsWith | counts),
            hours: (.hours | if type == "array" and length == 24 then map(number) else error("not 24 hours") end),
            beside: (.beside | many(text)),
            bests: {
              buildingDay: (.bests.buildingDay | maybe({day: (.day | day), placed: (.placed | number)})),
              deepest: (.bests.deepest | maybe(number)),
              highest: (.bests.highest | maybe(number))
            }
          }))
        }' "$state/counted.tmp" > "$counted.tmp" 2>/dev/null &&
      [ -s "$counted.tmp" ] && mv "$counted.tmp" "$counted"; then
    line="What the ledger counts is up to date."
  else
    line="Could not ask the ledger what it counts, so the page keeps what it has."
  fi
  rm -f "$counted.tmp" "$state/counted.tmp"
  [ "$line" = "$counted_log" ] || echo "$line"
  counted_log=$line
}

# Names by UUID, kept from the last round that could read them.
names='{}'

build() {
  dir=$server/$world/players/stats
  set -- "$dir"/*.json
  if [ ! -e "$1" ]; then
    say "Nobody has statistics in $dir yet."
    return
  fi

  update_database
  update_addresses
  update_countries
  # Country codes by name.
  countries=$(awk 'NR == FNR { code[$1] = $2; next } ($2 in code) && code[$2] != "-" { print $1, code[$2] }' \
    "$state/countries" "$state/addresses" 2>/dev/null |
    jq -cnR '[inputs | split(" ") | {key: .[0], value: .[1]}] | from_entries' 2>/dev/null) || countries='{}'

  # Bedrock players have no skin at Mojang. Floodgate gives them UUIDs that start with zeros.
  if [ "$fetch" = 1 ]; then
    for code in $(printf %s "$countries" | jq -r '[.[]] | unique | join(" ")' 2>/dev/null); do
      case $code in
        [A-Z][A-Z]) update_flag "$code" ;;
      esac
    done
    for file in "$@"; do
      uuid=$(basename "$file" .json)
      case $uuid in
        00000000-0000-0000-*) ;;
        *) update_skin "$uuid" ;;
      esac
    done
  fi
  # Models by UUID, for the skins that are there.
  models=$(for file in "$skins"/*.png; do
      [ -e "$file" ] || continue
      uuid=$(basename "$file" .png)
      printf '%s %s\n' "$uuid" "$(cat "$state/skin-$uuid" 2>/dev/null)"
    done | jq -cnR '[inputs | split(" ") | {key: .[0], value: (if .[1] == "slim" then "slim" else "wide" end)}] | from_entries' 2>/dev/null) || models='{}'

  # The whitelist has everyone who may join, and the server's cache has the name
  # each player last joined with, so the cache wins where they differ.
  if new=$(for file in whitelist.json usercache.json; do
      [ -r "$server/$file" ] && cat "$server/$file"
    done | jq -cs 'add // [] | map(select(.uuid and .name) | {key: .uuid, value: .name}) | from_entries' 2>/dev/null); then
    names=$new
  fi

  # When each file was last saved, which is when its player last played.
  seen=$(stat -c '%n %Y' "$@" 2>/dev/null | jq -cnR '
    [inputs | capture("(?<uuid>[^/]+)\\.json (?<at>[0-9]+)$") | {key: .uuid, value: (.at | tonumber)}] | from_entries
  ' 2>/dev/null) || seen='{}'

  # A file the game is writing at this moment does not parse. Then the last good
  # copy stays, and the next round tries again.
  if ! jq -cn --argjson names "$names" --argjson seen "$seen" --argjson countries "$countries" \
      --argjson models "$models" --argjson now "$(date +%s)" '
      {
        updated: $now,
        players: [
          inputs as $file
          | (input_filename | split("/") | last | rtrimstr(".json")) as $uuid
          | {
              uuid: $uuid,
              name: $names[$uuid],
              seen: $seen[$uuid],
              country: $countries[$names[$uuid] // ""],
              skin: $models[$uuid],
              # Of everything the game counts for a player, only the time played
              # reaches the page. The rest stays in the file on the server: what the
              # hub shows besides comes from the ledger, and a count such as how
              # often a member has died is not for everyone to read.
              # (No apostrophes in here: this program sits in single quotes.)
              stats: {custom: {play_time: (($file.stats["minecraft:custom"]["minecraft:play_time"] | numbers) // 0)}}
            }
        ]
      }' "$@" > "$out.tmp" 2>/dev/null; then
    say "Could not read the statistics in $dir, so the page keeps its last numbers."
    return
  fi
  if mv "$out.tmp" "$out"; then
    say "The statistics are up to date."
  else
    say "Could not write $out."
  fi
}

mkdir -p "$skins" "$flags" "$state" 2>/dev/null
trap 'kill $! 2>/dev/null; exit 0' TERM INT
while :; do
  build
  update_feats
  update_counted
  [ -z "${ONCE:-}" ] || exit 0
  sleep "$interval" &
  wait $!
done
