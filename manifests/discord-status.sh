#!/bin/sh
# Keeps one message in a Discord channel showing whether the server is up and who
# is playing (see README). It runs beside the server, reads the player list over
# RCON on the pod's loopback, and edits the message through a webhook every minute.
#
# The webhook URL comes from a Secret volume and is read again every round, so
# creating or changing that Secret needs no restart. The message's ID is kept on the
# data volume, so after a restart the same message is edited instead of a new one
# posted. If the message is deleted, a new one is posted.
#
# This must keep running whatever Discord or the server does. A container that
# stops makes the pod unready, which takes the server off the tunnel.

webhook_file=${WEBHOOK_FILE:-/run/discord-status/webhook-url}
id_file=${MESSAGE_ID_FILE:-/data/discord-status-message-id}
interval=${INTERVAL:-60}
name=${SERVER_NAME:-Lush Acres}

# Logs a line only when it differs from the last one, so a problem that lasts is
# logged once rather than every minute.
last_log=
say() {
  [ "$1" = "$last_log" ] || echo "$1"
  last_log=$1
}

# Builds the message for a state: online, starting or stopping. For online, $2 is
# the server's reply to the list command.
render() {
  jq -cn --arg state "$1" --arg list "${2-}" --arg name "$name" --argjson now "$(date +%s)" '
    def escape: gsub("(?<c>[\\\\*_~`|])"; "\\\(.c)");
    ([$list | capture("There are (?<n>[0-9]+) of a max of (?<max>[0-9]+) players online:(?<names>.*)")] | first) as $l
    | ($l.names // "" | split(",") | map(gsub("^ +| +$"; "")) | map(select(. != "")) | sort_by(ascii_downcase)) as $players
    | {
        online: {
          title: "🟢 \($name) is online",
          color: 3066993,
          text: (
            if $l == null then "The server is up."
            elif ($players | length) == 0 then "Nobody is playing right now."
            else "**\($l.n) of \($l.max) playing**\n" + ($players | map("• " + escape) | join("\n"))
            end
          )
        },
        starting: {
          title: "🟡 \($name) is starting",
          color: 15844367,
          text: "It cannot be joined yet."
        },
        stopping: {
          title: "🔴 \($name) is stopping",
          color: 15158332,
          text: "It is restarting or shutting down. This message changes when it is back."
        }
      }
    | .[$state]
    | {
        embeds: [{title, color, description: "\(.text)\n\nUpdated <t:\($now):R>"}],
        allowed_mentions: {parse: []}
      }'
}

# Sends one request to Discord, and sets $code and $body from the reply. The URL
# holds the webhook's token, so it is never logged.
request() {
  reply=$(curl --silent --show-error --max-time 15 --request "$1" \
    --header 'Content-Type: application/json' --data "$3" \
    --write-out '\n%{http_code}' "$2" 2>&1)
  code=$(printf '%s' "$reply" | tail -n 1)
  body=$(printf '%s' "$reply" | sed '$d')
}

# Edits the message, or posts it if there is none yet.
message_id=
publish() {
  if [ ! -r "$webhook_file" ]; then
    say "The discord-status Secret has no webhook-url yet, so nothing is posted."
    return
  fi
  url=$(tr -d ' \r\n' < "$webhook_file")
  case $url in
    https://*/webhooks/*) url=${url%/} ;;
    *) say "The discord-status Secret's webhook-url is not a Discord webhook URL."; return ;;
  esac

  [ -n "$message_id" ] || message_id=$(cat "$id_file" 2>/dev/null)
  if [ -n "$message_id" ]; then
    request PATCH "$url/messages/$message_id" "$1"
    case $code in
      2??) say "The status message is up to date."; return ;;
      404) ;; # Deleted, or the webhook was replaced: post a new one below.
      *) say "Discord answered $code to editing the status message: $(printf '%s' "$body" | head -c 300)"; return ;;
    esac
  fi

  request POST "$url?wait=true" "$1"
  case $code in
    2??) message_id=$(printf '%s' "$body" | jq -r '.id // empty' 2>/dev/null) ;;
    *) say "Discord answered $code to posting the status message: $(printf '%s' "$body" | head -c 300)"; return ;;
  esac
  if [ -z "$message_id" ]; then
    say "Discord posted the status message but did not say its ID."
    return
  fi
  say "Posted status message $message_id."
  # Also kept in memory above, so a failed write does not post a new message every minute.
  { printf '%s\n' "$message_id" > "$id_file.tmp" && mv "$id_file.tmp" "$id_file"; } ||
    say "Could not save the status message's ID in $id_file."
}

stop=
trap 'stop=1' TERM INT
while :; do
  if list=$(timeout 10 rcon-cli list 2>/dev/null); then
    publish "$(render online "$list")"
  else
    publish "$(render starting)"
  fi
  [ -z "$stop" ] || break
  sleep "$interval" &
  wait $!
  [ -z "$stop" ] || break
done
kill $! 2>/dev/null
publish "$(render stopping)"
