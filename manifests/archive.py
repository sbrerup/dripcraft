"""Keeps what the survival server knows about what players do (see README).

The server records far more than it keeps. Prism, the grief log, notes every
block, chest, trade and death with who, where and when, and deletes it after six
weeks. The web map knows where everyone is, and keeps nothing. The game counts
what each player has done, as totals with no history. This gathers all three
into one database that only grows, so that what happened can still be asked
about later:

- every row Prism writes, in tables shaped like Prism's own;
- where each player on the map is, every few seconds;
- each player's statistics file, every time the game has saved it.

Nothing is worked out here. The tables hold what the sources said, so a mistake
in reading them can be put right later without having lost anything.

The server's folder is mounted read-only, and Prism's database is opened
read-only, so nothing here can change the world or the grief log. This must keep
running whatever is in those files and whatever the server answers.

It also answers one question, for the ledger, the application that is built on
this: which rows has Prism written after a given one? That is the feed. It gives
rows out and takes nothing in, and it reads the archive through a connection of
its own that cannot write. Whatever happens to the feed, the gathering goes on.
"""
import http.server
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib

SERVER = os.environ.get("SERVER_DIR", "/server")
WORLD = os.environ.get("WORLD", "dripcraft")
ARCHIVE = os.environ.get("ARCHIVE_FILE", "/archive/archive.db")
PRISM = os.environ.get("PRISM_FILE", SERVER + "/plugins/prism/prism.db")
# squaremap's list of who is on the map, which it serves and does not write to disk.
PLAYERS_URL = os.environ.get("PLAYERS_URL", "http://minecraft:8080/tiles/players.json")
# How many seconds pass between looks at each source.
POSITIONS_EVERY = float(os.environ.get("POSITIONS_EVERY", "10"))
EVENTS_EVERY = float(os.environ.get("EVENTS_EVERY", "60"))
TOTALS_EVERY = float(os.environ.get("TOTALS_EVERY", "60"))
# Prism's rows are copied this many at a time, so reading never holds its database long.
BATCH = int(os.environ.get("BATCH", "20000"))
# One look at each source and then stop, for the tests.
ONCE = bool(os.environ.get("ONCE"))
# Where the feed is answered. The archive's Service and network policy name the same port.
FEED_PORT = int(os.environ.get("FEED_PORT", "8080"))
# How many rows an answer of the feed holds when the question does not say, and at the most.
FEED_ROWS = 1000
FEED_MOST = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v) WITHOUT ROWID;
-- Everyone who has been seen on the map.
CREATE TABLE IF NOT EXISTS players (player INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE, name TEXT);
-- Where each player on the map was at a moment, as the map said it.
CREATE TABLE IF NOT EXISTS positions (
  t INTEGER NOT NULL, player INTEGER NOT NULL, world TEXT, x INTEGER, y INTEGER, z INTEGER,
  yaw INTEGER, health INTEGER, armor INTEGER, PRIMARY KEY (t, player)
) WITHOUT ROWID;
-- The stretches of time during which the map was being read. A player with no
-- position inside one was not on the map. Outside them, nobody was looking.
CREATE TABLE IF NOT EXISTS watches (start INTEGER PRIMARY KEY, "end" INTEGER NOT NULL, samples INTEGER NOT NULL);
-- A player's statistics file as the game saved it at t, compressed with zlib.
CREATE TABLE IF NOT EXISTS totals (uuid TEXT NOT NULL, t INTEGER NOT NULL, stats BLOB NOT NULL, PRIMARY KEY (uuid, t));
"""


class Quiet(Exception):
    """Something expected, such as a server that is not up yet."""


last_said = None


def say(line):
    """Logs a line only when it differs from the last, so a lasting problem is logged once."""
    global last_said
    if line != last_said:
        print(line, flush=True)
    last_said = line


def open_archive():
    os.makedirs(os.path.dirname(ARCHIVE) or ".", exist_ok=True)
    db = sqlite3.connect(ARCHIVE, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-8000")
    db.executescript(SCHEMA)
    db.commit()
    return db


def open_prism():
    """Opens Prism's database for reading only, while the server may be writing to it."""
    if not os.path.exists(PRISM):
        raise Quiet("Prism has no database yet, so there are no events to copy.")
    address = "file:" + urllib.parse.quote(PRISM) + "?mode=ro"
    try:
        db = sqlite3.connect(address, uri=True, timeout=10)
        db.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        # After a clean stop the server has folded its log of recent changes into
        # the database and removed it. From a folder that cannot be written to, the
        # database can then only be opened as one that nobody is changing.
        log = PRISM + "-wal"
        if os.path.exists(log) and os.path.getsize(log) > 0:
            raise
        db = sqlite3.connect(address + "&immutable=1", uri=True, timeout=10)
        db.execute("SELECT count(*) FROM sqlite_master").fetchone()
    db.execute("PRAGMA cache_size=-8000")
    return db


def get(archive, key, default=None):
    row = archive.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return default if row is None else row[0]


def put(archive, key, value):
    archive.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))


def named(name):
    """A table or column name from Prism, which is put into statements as it is."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("unexpected name in Prism's database: " + repr(name))
    return '"' + name + '"'


def shape(archive, prism, table):
    """Gives the archive's copy of one of Prism's tables every column Prism's has.

    The copy has the same columns under the same names, and "source" in front. So
    a newer Prism with more columns is followed without a change here. Returns the
    names of Prism's columns, the first of which is the row's number.
    """
    wanted = [(row[1], row[2] or "") for row in prism.execute(f"PRAGMA table_info({named(table)})")]
    if not wanted:
        raise Quiet(f"Prism's database has no {table} yet.")
    have = [row[1] for row in archive.execute(f"PRAGMA table_info({named(table)})")]
    if not have:
        columns = ", ".join(f"{named(name)} {kind}" for name, kind in wanted)
        archive.execute(
            f"CREATE TABLE {named(table)} (source INTEGER NOT NULL, {columns}, PRIMARY KEY (source, {named(wanted[0][0])}))"
        )
    else:
        for name, kind in wanted:
            if name not in have:
                archive.execute(f"ALTER TABLE {named(table)} ADD COLUMN {named(name)} {kind}")
    return [name for name, _ in wanted]


def copy_events(archive, now):
    """Copies the rows Prism has written since the last look.

    Prism numbers its rows upwards and deletes the oldest, so the copy carries on
    from the highest number it has. If Prism's database is ever replaced, its
    numbers start again and mean other things. Then a new "source" starts, so old
    and new rows never pass for each other.
    """
    prism = open_prism()
    try:
        names = shape(archive, prism, "prism_activities")
        if "activity_id" not in names or "timestamp" not in names:
            raise Quiet("Prism's activities are not shaped as expected, so they are not copied.")
        source = int(get(archive, "prism_source", 0))
        last = int(get(archive, "prism_last_id", 0))
        if last:
            # The last row copied is still there, as it was, unless the database is another one.
            same = prism.execute("SELECT timestamp FROM prism_activities WHERE activity_id = ?", (last,)).fetchone()
            top = prism.execute("SELECT max(activity_id) FROM prism_activities").fetchone()[0] or 0
            try:
                # The highest number ever given out, which outlasts the rows themselves.
                given = prism.execute("SELECT seq FROM sqlite_sequence WHERE name = 'prism_activities'").fetchone()
                top = max(top, given[0] if given else 0)
            except sqlite3.Error:
                pass
            if top < last or (same is not None and same[0] != get(archive, "prism_last_time")):
                source, last = source + 1, 0
                put(archive, "prism_source", source)
                print(f"Prism's database is not the one copied before, so its rows are kept as source {source}.", flush=True)

        columns = ", ".join(named(name) for name in names)
        marks = ", ".join("?" * (len(names) + 1))
        number, moment = names.index("activity_id"), names.index("timestamp")
        copied = 0
        while True:
            rows = prism.execute(
                f"SELECT {columns} FROM prism_activities WHERE activity_id > ? ORDER BY activity_id LIMIT ?", (last, BATCH)
            ).fetchall()
            if not rows:
                break
            archive.executemany(
                f"INSERT OR IGNORE INTO prism_activities (source, {columns}) VALUES ({marks})", [(source, *row) for row in rows]
            )
            last = rows[-1][number]
            put(archive, "prism_source", source)
            put(archive, "prism_last_id", last)
            put(archive, "prism_last_time", rows[-1][moment])
            archive.commit()
            copied += len(rows)
        archive.execute("CREATE INDEX IF NOT EXISTS prism_activities_time ON prism_activities (timestamp)")

        # What the numbers in those rows stand for: the names of actions, blocks,
        # items, creatures, players and worlds. These tables are small. New rows are
        # copied every time, and all of them again each hour, for a name that changed.
        again = now - int(get(archive, "prism_names_at", 0)) >= 3600
        tables = [row[0] for row in prism.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'prism%'")]
        for table in tables:
            if table == "prism_activities":
                continue
            names = shape(archive, prism, table)
            columns = ", ".join(named(name) for name in names)
            marks = ", ".join("?" * (len(names) + 1))
            first = named(names[0])
            have = None if again else archive.execute(
                f"SELECT max({first}) FROM {named(table)} WHERE source = ?", (source,)
            ).fetchone()[0]
            where, values = ("", ()) if have is None else (f" WHERE {first} > ?", (have,))
            rows = prism.execute(f"SELECT {columns} FROM {named(table)}{where}", values).fetchall()
            archive.executemany(
                f"INSERT OR REPLACE INTO {named(table)} (source, {columns}) VALUES ({marks})", [(source, *row) for row in rows]
            )
        if again:
            put(archive, "prism_names_at", now)
        # Every row copied so far now has what its numbers stand for here as well,
        # so the feed may give it out. Written as the feed's cursors are: the
        # source, then the row's number.
        put(archive, "prism_named", f"{source}:{last}")
        archive.commit()
        return copied
    finally:
        prism.close()


def dashed(uuid):
    """A UUID in the form the game's files and Prism use. The map leaves the dashes out."""
    plain = str(uuid).replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", plain):
        return None
    return "-".join((plain[:8], plain[8:12], plain[12:16], plain[16:20], plain[20:]))


def whole(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def sample_positions(archive, now):
    """Notes where everyone on the map is right now."""
    try:
        with urllib.request.urlopen(PLAYERS_URL, timeout=3) as reply:
            listed = json.load(reply).get("players") or []
    except Exception as error:
        raise Quiet(f"The map's list of players cannot be read ({error}), so nobody's position is noted.") from None
    seen = 0
    for one in listed:
        uuid = dashed(one.get("uuid", "")) if isinstance(one, dict) else None
        if not uuid:
            continue
        name = one.get("name")
        archive.execute("INSERT OR IGNORE INTO players (uuid, name) VALUES (?, ?)", (uuid, name))
        archive.execute("UPDATE players SET name = ? WHERE uuid = ? AND name IS NOT ?", (name, uuid, name))
        player = archive.execute("SELECT player FROM players WHERE uuid = ?", (uuid,)).fetchone()[0]
        archive.execute(
            "INSERT OR REPLACE INTO positions (t, player, world, x, y, z, yaw, health, armor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, player, one.get("world"), whole(one.get("x")), whole(one.get("y")), whole(one.get("z")),
             whole(one.get("yaw")), whole(one.get("health")), whole(one.get("armor"))),
        )
        seen += 1
    # The stretch of watching carries on, unless the last look was too long ago.
    latest = archive.execute('SELECT start, "end" FROM watches ORDER BY start DESC LIMIT 1').fetchone()
    if latest and 0 <= now - latest[1] <= 3 * POSITIONS_EVERY:
        archive.execute('UPDATE watches SET "end" = ?, samples = samples + 1 WHERE start = ?', (now, latest[0]))
    else:
        archive.execute('INSERT OR REPLACE INTO watches (start, "end", samples) VALUES (?, ?, 1)', (now, now))
    archive.commit()
    return seen


saved = {}


def save_totals(archive, now):
    """Keeps each player's statistics file as the game last saved it.

    The game writes a player's file when it saves the world and when they leave,
    so a file changes about every five minutes while its player is on.
    """
    folder = os.path.join(SERVER, WORLD, "players", "stats")
    try:
        files = [entry for entry in os.scandir(folder) if entry.name.endswith(".json")]
    except OSError:
        raise Quiet(f"Nobody has statistics in {folder} yet.") from None
    if not saved:
        saved.update(archive.execute("SELECT uuid, max(t) FROM totals GROUP BY uuid").fetchall())
    kept = 0
    for entry in files:
        uuid = entry.name[:-5]
        try:
            at = int(entry.stat().st_mtime)
            if saved.get(uuid) == at:
                continue
            with open(entry.path, "rb") as file:
                text = file.read()
            # A file the game is writing at this moment is not whole. The next look takes it.
            json.loads(text)
        except (OSError, ValueError):
            continue
        archive.execute("INSERT OR IGNORE INTO totals (uuid, t, stats) VALUES (?, ?, ?)", (uuid, at, zlib.compress(text, 9)))
        saved[uuid] = at
        kept += 1
    archive.commit()
    return kept


# The feed. Each question is answered through a connection of its own that can
# only read, so nothing asked here can change the archive or hold the gathering up.

# What the numbers in one of Prism's rows stand for: the name it gets here, the
# column that holds the number, the table that says what it stands for, that
# table's own number, and the columns wanted from it.
NAMES = (
    ("action", "action_id", "prism_actions", "action_id", ("action",)),
    ("world", "world_id", "prism_worlds", "world_id", ("world",)),
    ("block", "affected_block_id", "prism_blocks", "block_id", ("ns", "name", "data")),
    ("replaced", "replaced_block_id", "prism_blocks", "block_id", ("ns", "name")),
    ("item", "affected_item_id", "prism_items", "item_id", ("material",)),
    ("entity", "affected_entity_type_id", "prism_entity_types", "entity_type_id", ("entity_type",)),
    ("player", "affected_player_id", "prism_players", "player_id", ("player_uuid", "player")),
    ("cause", "cause_id", "prism_causes", "cause_id", ("cause",)),
    ("cause_player", "cause_player_id", "prism_players", "player_id", ("player_uuid", "player")),
    ("cause_entity", "cause_entity_type_id", "prism_entity_types", "entity_type_id", ("entity_type",)),
    ("cause_block", "cause_block_id", "prism_blocks", "block_id", ("ns", "name")),
)
# The columns of a row that are given as they are. Three are not among them and
# are never given out, because they can hold what a member wrote, on a sign, a
# name tag or an item they renamed: "descriptor", "metadata" and what Prism keeps
# of the thing itself, "serialized_data".
PLAIN = ("x", "y", "z", "affected_item_quantity", "reversed")


def cursor(text):
    """The source and number of the row a cursor stands for. An empty one comes before every row."""
    if not text:
        return (-1, 0)
    if not re.fullmatch(r"[0-9]{1,18}:[0-9]{1,18}", text):
        raise ValueError("after is not a cursor that this feed gave out")
    source, number = text.split(":")
    return (int(source), int(number))


def feed_statement(db):
    """The question that reads rows for the feed, and what each column of its answer is.

    It is made from the columns the archive has, which are Prism's own. So a
    Prism with other columns is followed here too, with less in each row. Rows
    are found by their number and come in that order, so nothing is sorted, and
    no more is read than is given.
    """
    columns = {}

    def of(table):
        if table not in columns:
            columns[table] = {row[1] for row in db.execute(f"PRAGMA table_info({named(table)})")}
        return columns[table]

    have = of("prism_activities")
    if not {"source", "activity_id", "timestamp"} <= have:
        return None, None
    picked = ["a.source", "a.activity_id", "a.timestamp"]
    keys = ["source", "id", "at"]
    joins = []
    for column in PLAIN:
        if column in have:
            picked.append(f"a.{named(column)}")
            keys.append(column)
    for short, column, table, number, wanted in NAMES:
        if column not in have or not {"source", number} <= of(table):
            continue
        joins.append(
            f"LEFT JOIN {named(table)} {named(short)} ON {named(short)}.source = a.source"
            f" AND {named(short)}.{named(number)} = a.{named(column)}"
        )
        for name in wanted:
            if name in of(table):
                picked.append(f"{named(short)}.{named(name)}")
                keys.append(f"{short}.{name}")
    statement = (
        f"SELECT {', '.join(picked)} FROM prism_activities a {' '.join(joins)}"
        " WHERE (a.source, a.activity_id) > (?, ?) AND (a.source, a.activity_id) <= (?, ?)"
        " ORDER BY a.source, a.activity_id LIMIT ?"
    )
    return statement, keys


def feed_row(got):
    """One of Prism's rows as the feed gives it: names for numbers, and nothing that is empty."""

    def block(short):
        name = got.get(short + ".name")
        if name is None:
            return None
        space = got.get(short + ".ns")
        return f"{space}:{name}" if space else name

    def player(short):
        known = (("uuid", got.get(short + ".player_uuid")), ("name", got.get(short + ".player")))
        return {key: value for key, value in known if value is not None} or None

    row = {
        "cursor": f"{got['source']}:{got['id']}",
        "source": got["source"],
        "id": got["id"],
        "at": got["at"],
        "action": got.get("action.action"),
        "world": got.get("world.world"),
        "x": got.get("x"),
        "y": got.get("y"),
        "z": got.get("z"),
        "block": block("block"),
        "blockData": got.get("block.data") or None,
        "replacedBlock": block("replaced"),
        "item": got.get("item.material"),
        "quantity": got.get("affected_item_quantity"),
        "entity": got.get("entity.entity_type"),
        "player": player("player"),
        "cause": got.get("cause.cause"),
        "causePlayer": player("cause_player"),
        "causeEntity": got.get("cause_entity.entity_type"),
        "causeBlock": block("cause_block"),
    }
    if got.get("reversed") is not None:
        row["reversed"] = bool(got["reversed"])
    return {key: value for key, value in row.items() if value is not None}


def feed_page(after="", until=None, limit=None):
    """Prism's rows after a cursor, in the order they were recorded, and the cursor to ask with next.

    The rows end before the first one that happened later than "until", and none
    is passed over to reach a later one. A caller that stays a little behind the
    present therefore gets every row, in order. A row is given only once what its
    numbers stand for has been copied as well. With nothing to give, the cursor
    to ask with next is the one that was asked with.
    """
    start = cursor(after)
    limit = max(1, min(FEED_MOST, FEED_ROWS if limit is None else limit))
    rows = []
    if not os.path.exists(ARCHIVE):
        return rows, after
    address = "file:" + urllib.parse.quote(ARCHIVE) + "?mode=ro"
    db = sqlite3.connect(address, uri=True, timeout=10)
    try:
        db.execute("PRAGMA cache_size=-2000")
        statement, keys = feed_statement(db)
        if statement is None:
            return rows, after
        try:
            end = cursor(get(db, "prism_named") or "")
        except (sqlite3.OperationalError, ValueError):
            return rows, after
        for found in db.execute(statement, (*start, *end, limit)):
            got = dict(zip(keys, found))
            if until is not None and isinstance(got["at"], (int, float)) and got["at"] > until:
                break
            rows.append(feed_row(got))
        return rows, rows[-1]["cursor"] if rows else after
    finally:
        db.close()


def asked_number(given, name):
    """A whole number in a question to the feed, or None when it is not there."""
    text = given.get(name, [""])[-1]
    if not text:
        return None
    if not re.fullmatch(r"[0-9]{1,18}", text):
        raise ValueError(f"{name} is not a whole number")
    return int(text)


class Feed(http.server.BaseHTTPRequestHandler):
    """Answers the feed's one question. Only GET is understood, so nothing can be sent in."""

    # A caller that connects and then says nothing is let go of.
    timeout = 30

    def do_GET(self):
        try:
            asked = urllib.parse.urlsplit(self.path)
        except ValueError:
            return self.answer(400, {"error": "The address cannot be read."})
        if asked.path != "/feed/activities":
            return self.answer(404, {"error": "There is nothing at this address."})
        given = urllib.parse.parse_qs(asked.query, keep_blank_values=True)
        try:
            after = given.get("after", [""])[-1]
            cursor(after)
            until = asked_number(given, "until")
            limit = asked_number(given, "limit")
        except ValueError as problem:
            return self.answer(400, {"error": str(problem)})
        try:
            rows, last = feed_page(after, until, limit)
        except Exception as problem:
            say(f"The feed could not answer: {type(problem).__name__}: {problem}")
            return self.answer(500, {"error": "The archive could not be read just now."})
        return self.answer(200, {"rows": rows, "next": last})

    def answer(self, status, body):
        text = json.dumps(body, separators=(",", ":")).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)
        except OSError:
            pass  # The caller has gone.

    def log_message(self, *args):
        """Questions are not logged. The log is for what the gathering has to say."""


def serve_feed():
    """Answers the feed for as long as the pod runs, and starts again if it cannot."""
    while True:
        try:
            with http.server.ThreadingHTTPServer(("", FEED_PORT), Feed) as server:
                print(f"Answering the feed on port {server.server_address[1]}.", flush=True)
                server.serve_forever()
        except Exception as problem:
            say(f"The feed cannot be answered: {type(problem).__name__}: {problem}")
        time.sleep(30)


TASKS = {
    "positions": (POSITIONS_EVERY, sample_positions),
    "events": (EVENTS_EVERY, copy_events),
    "snapshots": (TOTALS_EVERY, save_totals),
}


def main():
    # As the first process in a container, this is not stopped by a request to
    # stop unless it asks to be.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if not ONCE:
        # Beside the gathering, and ended with it. Nothing it does can stop the loop below.
        threading.Thread(target=serve_feed, daemon=True).start()
    archive = None
    due = dict.fromkeys(TASKS, 0.0)
    done = dict.fromkeys(TASKS, 0)
    told = time.time()
    while True:
        try:
            if archive is None:
                archive = open_archive()
                print(f"Keeping the archive in {ARCHIVE}.", flush=True)
            for name, (every, task) in TASKS.items():
                now = time.time()
                if now < due[name]:
                    continue
                due[name] = now + every
                try:
                    done[name] += task(archive, int(now))
                except Quiet as problem:
                    archive.rollback()
                    say(str(problem))
                except Exception as problem:
                    archive.rollback()
                    say(f"Could not keep the {name} this time: {type(problem).__name__}: {problem}")
        except Exception as problem:  # The archive itself cannot be opened or written.
            say(f"The archive cannot be used: {type(problem).__name__}: {problem}")
            try:
                archive.close()
            except Exception:
                pass
            archive = None
        if ONCE:
            print(", ".join(f"{count} {name}" for name, count in done.items()), flush=True)
            return
        if time.time() - told >= 3600:
            say("In the last hour: " + ", ".join(f"{count} {name}" for name, count in done.items()) + ".")
            done = dict.fromkeys(TASKS, 0)
            told = time.time()
        time.sleep(max(0.5, min(due.values()) - time.time()) if archive else 30)


if __name__ == "__main__":
    main()
