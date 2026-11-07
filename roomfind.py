#!/usr/bin/env python3
"""roomfind — answer "what course is happening in this room at this time?" from u:find.

Strategy: the m-ufind calendar endpoint returns a whole semester's schedule as a
single iCal download (room, time, course id/name, group, u:find URL per session):

    https://m-ufind.univie.ac.at/courses/calendar/query.ics?query=2026W%20c%3A10000

We parse that into a SQLite DB, then room+time lookups are trivial local queries.

Usage:
    python roomfind.py download 2026W [-o roomfind.db]   # fetch + index a semester
    python roomfind.py find "Hörsaal 42" 2026-10-06 14:00 [-d roomfind.db]
    python roomfind.py rooms "Hörsaal" [-d roomfind.db]  # list matching rooms
"""
import argparse
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime

BASE = "https://m-ufind.univie.ac.at/courses/calendar/query.ics"
# c:10000 covers ~7.2k courses/semester; c: above ~10k-20k silently returns nothing
COURSE_CAP = 10000

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    uid       TEXT,
    course_id TEXT,
    group_no  TEXT,
    kind      TEXT,
    title     TEXT,
    room      TEXT,
    dtstart   TEXT,
    dtend     TEXT,
    url       TEXT
);
CREATE INDEX IF NOT EXISTS idx_room  ON events (room);
CREATE INDEX IF NOT EXISTS idx_start ON events (dtstart);
"""


def unfold(text):
    """RFC 5545 line unfolding: continuation lines start with a space/tab and
    belong to the previous line, so join them before parsing."""
    # Normalize line endings first. Some calendars arrive with stray carriage
    # returns ("\r\r\n"), which splitlines() would split into an extra empty
    # line and break the continuation join below (the continuation then lands
    # on the empty line instead of its parent, and is silently dropped).
    text = re.sub(r"\r+\n", "\n", text).replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def unescape(v):
    return v.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").replace("\\n", "\n")


def parse_ics(text):
    """Yield one event dict per VEVENT in a (mostly) line-based way."""
    events, cur, prop = [], None, None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = dict.fromkeys(
                ("uid", "course_id", "group_no", "kind", "title", "room",
                 "dtstart", "dtend", "url"), "")
            continue
        if line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.split(";")[0].upper()
        value = unescape(value)
        if name == "DTSTART":
            cur["dtstart"] = value
        elif name == "DTEND":
            cur["dtend"] = value
        elif name == "LOCATION":
            cur["room"] = value.strip()
        elif name == "SUMMARY":
            m = re.match(r"(\d{6})-(\d+)\s+(\S+)\s+(.*)", value)
            if m:
                cur.update(course_id=m.group(1), group_no=m.group(2),
                           kind=m.group(3), title=m.group(4))
            else:
                cur["title"] = value
        elif name == "URL":
            cur["url"] = value.strip()
        elif name == "UID":
            cur["uid"] = value
    return events


def dt_to_sql(s):
    """20261006T131500 -> 2026-10-06T13:15:00 (lexicographically comparable)."""
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}"


def download(term, db_path):
    url = f"{BASE}?query={term}%20c%3A{COURSE_CAP}"
    print(f"Fetching {url} ... (this can take 1-5 minutes, response is ~30-80 MB)")
    req = urllib.request.Request(url, headers={"User-Agent": "roomfind/0.1"})
    with urllib.request.urlopen(req, timeout=600) as r:
        text = r.read().decode("utf-8", "replace")
    events = parse_ics(text)
    print(f"Parsed {len(events)} sessions.")
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    db.execute("DELETE FROM events")
    db.executemany(
        "INSERT INTO events VALUES (:uid,:course_id,:group_no,:kind,:title,:room,"
        ":dtstart,:dtend,:url)",
        [{**e,
          "dtstart": dt_to_sql(e.get("dtstart", "")),
          "dtend": dt_to_sql(e.get("dtend", "")),
          "room": e.get("room", "")} for e in events],
    )
    db.commit()
    n_courses = db.execute("SELECT COUNT(DISTINCT course_id) FROM events").fetchone()[0]
    n_rooms = db.execute("SELECT COUNT(DISTINCT room) FROM events").fetchone()[0]
    print(f"Indexed {n_courses} courses across {n_rooms} rooms into {db_path}")
    db.close()


def find(room, date, time_, db_path):
    t = f"{date}T{time_}:00"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT * FROM events
           WHERE room LIKE ? COLLATE NOCASE AND dtstart <= ? AND dtend > ?
           ORDER BY dtstart""",
        (f"%{room}%", t, t),
    ).fetchall()
    if not rows:
        print(f"No session in a room matching '{room}' at {date} {time_}.")
        return
    for r in rows:
        print(f"{r['room']}\n  {r['dtstart'][11:16]}-{r['dtend'][11:16]}  "
              f"{r['course_id']}-{r['group_no']} {r['kind']} {r['title']}\n  {r['url']}")
    db.close()


def rooms(pattern, db_path):
    db = sqlite3.connect(db_path)
    for (room, n) in db.execute(
        "SELECT room, COUNT(*) FROM events WHERE room LIKE ? GROUP BY room ORDER BY room",
        (f"%{pattern}%",),
    ):
        print(f"{n:5d}  {room}")
    db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download + index a semester")
    d.add_argument("term", help='e.g. "2026W" or "+ct" (current term)')
    d.add_argument("-o", "--out", default="roomfind.db", help="SQLite DB path")
    f = sub.add_parser("find", help="what's on in a room at a given time")
    f.add_argument("room")
    f.add_argument("date", help="YYYY-MM-DD")
    f.add_argument("time", help="HH:MM")
    f.add_argument("-d", "--db", default="roomfind.db", help="SQLite DB path")
    r = sub.add_parser("rooms", help="list rooms matching a substring")
    r.add_argument("pattern", nargs="?", default="")
    r.add_argument("-d", "--db", default="roomfind.db", help="SQLite DB path")
    args = ap.parse_args()
    db_path = args.out if args.cmd == "download" else args.db
    if args.cmd == "download":
        download(args.term, db_path)
    elif args.cmd == "find":
        find(args.room, args.date, args.time, db_path)
    elif args.cmd == "rooms":
        rooms(args.pattern, db_path)


if __name__ == "__main__":
    main()
