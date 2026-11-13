#!/bin/sh
# Writes the numbers for the player statistics page (see README). It runs beside
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
# The server's folder is mounted read-only. This must keep running whatever is in
# it, and whatever Mojang or DB-IP answer. A container that stops makes the pod
# unready, which takes the web map off the tunnel.

server=${SERVER_DIR:-/server}
world=${WORLD:-dripcraft}
out=${OUT_FILE:-/out/stats.json}
state=${STATE_DIR:-/state}
interval=${INTERVAL:-300}
# 0 turns off everything that uses the internet, for the tests.
fetch=${FETCH:-1}
skins=$(dirname "$out")/skins
flags=$(dirname "$out")/flags

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
      def short: with_entries(.key |= ltrimstr("minecraft:"));
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
              stats: (
                ($file.stats // {} | short) as $stats
                | ["custom", "mined", "crafted", "used", "broken", "killed", "killed_by"]
                | map({key: ., value: ($stats[.] // {} | short)})
                | from_entries
              )
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
  [ -z "${ONCE:-}" ] || exit 0
  sleep "$interval" &
  wait $!
done
