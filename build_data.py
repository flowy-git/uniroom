#!/usr/bin/env python3
"""Build the site data (events + room catalog) for the Uni Wien room finder.

Pipeline:
  1. Download course + exam calendars per term from the m-ufind calendar API
     (one ~30-80 MB request each; above c:10000 the endpoint silently returns
     an empty calendar, so a 0-event response is treated as an error).
  2. Parse the iCal files into a compact events list.
  3. Derive a room catalog (designation + building) from all LOCATION strings,
     flagging anything the heuristics can't classify for manual review.
  4. Emit site/data/events-<term>.json, site/data/rooms.json and
     rooms_review.md (for the eyeball pass).

Usage: python build_data.py [--no-download] [--fresh]
"""
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import roomfind

# Prefer certifi's CA bundle (uv's standalone CPython on Windows sometimes can't
# chain m-ufind's certificate from the stock store); TLS verification stays on.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

BASES = {
    "courses": "https://m-ufind.univie.ac.at/courses/calendar/query.ics",
    "exams": "https://m-ufind.univie.ac.at/exams/calendar/query.ics",
}
TERMS = ["2026S", "2026W"]
CAP = 10000
RAW_DIR = Path("data_raw")
OUT_DIR = Path("site/data")

FIELDS = ["course_id", "group", "kind", "title", "room", "start", "end", "src", "url"]

SUMMARY_RE = re.compile(r"^(\d{6})-(\d+)\s+(\S+)\s+(.*)$")
EXAM_SUMMARY_RE = re.compile(r"^(\d{6})-(\d+)\s*(.*)$")

# Buildings that appear in LOCATION strings, matched case-insensitively at a
# word boundary. Anything not matched here falls back to heuristics and is
# flagged "?" in rooms_review.md for the manual pass.
BUILDINGS = [
    "Hauptgebäude",
    "Oskar-Morgenstern-Platz",
    "Schenkenstraße",
    "Währingerstraße",
    "Währinger Gürtel",
    "Währinger Straße",
    "Spitalgasse",
    "Althanstraße",
    "Sensengasse",
    "Rooseveltplatz",
    "Universitätsstraße",
    "Garnisongasse",
    "Berggasse",
    "Liechtensteinstraße",
    "NIG",
    "Kolingasse",
    "Porzellangasse",
    "Schottengasse",
    "Campus",
    "Juridicum",
    "UZA2",
    "UZA 2",
    "UZA II",
    "UZA",
    "Djerassiplatz",
    "Boltzmanngasse",
    "Mühlgasse",
    "Ettenreichgasse",
    "Mayerweckstraße",
    "Grenzackerstraße",
    "Türkenschanzstraße",
    "Digital",
    "Online",
    "ZOOM",
    "ZSU - USZ I",
    "ZSU - USZ II",
    "Schulschiff, 1210 Wien",
    "Wiener Straße 38, 3100 St. Pölten",
    "Blindeninstitut Wittelsbachstr. 5",
    "Instituts für Osteuropäische Geschichte",
    "Djerassiplatz 1", 
    "Oskar-Morgenstern-Platz 1",
    "UniCampus Zugang Hof 2",
    "Hirnforschungzentrum Spitalgasse 4",
    "UniCampus",
    "Fakultätszentrum für Biodiversität",
    "Inst. f. Orientalistik UniCampus",
    "Franz-Klein-Gasse",
    "Inst. f. Kunstgeschichte",
    "Schweißraum",
    "ZfT Philippovichgasse 11",
    "Aichholzgasse",
    "Alser Straße",
    "Augasse",
    "Apostelgasse",
    "Josef-Holaubek-Platz",
    "Mariahilfer Straße",
    "Campus-Vienna-Biocenter",
    "Dr.-Bohr-Gasse",
    "Gymnasiumstraße",
    "Auf der Schmelz",
    "Trauttmansdorffgasse",
    "Universitätsring",
    "Postgasse",
    "Pramergasse",
    "Rathausstraße",
    "Rennweg",
    "Schlossbergweg",
    "Schottenbastei",
    "Schwarzspanierstraße",
    "Schönbrunner Straße",
    "Hanuschgasse",
    "Liebiggasse",
    "Philippovichgasse",
    "Wiener Straße",
]

# Official building designations from u:space Rauminfo (public list). Extracted
# buildings are canonicalized onto these names so the dropdowns show official
# designations (e.g. "UZA" -> "Althanstraße 14 (UZA I)").
OFFICIAL_BUILDINGS = [
    "Aichholzgasse 4",
    "Alser Straße 23",
    "Althanstraße 14 (UZA I)",
    "Apostelgasse 23",
    "Auf der Schmelz 6 (USZ I)",
    "Auf der Schmelz 6a (USZ II)",
    "Augasse 2-6",
    "Berggasse 7",
    "Hanuschgasse 3",
    "Josef-Holaubek-Platz 2 (UZA II)",
    "Kolingasse 14-16",
    "Liebiggasse 5",
    "Mariahilfer Straße 91",
    "Oskar-Morgenstern-Platz 1",
    "Campus-Vienna-Biocenter 5",
    "Djerassiplatz 1 (UBB)",
    "Dr.-Bohr-Gasse 3",
    "Dr.-Bohr-Gasse 9",
    "Franz-Klein-Gasse 1",
    "Gymnasiumstraße 50",
    "Sensengasse 3 (Stiege 2)",
    "Sensengasse 3a",
    "Sensengasse 8",
    "Spitalgasse 2",
    "Spitalgasse 4, Hof 10",
    "Trauttmansdorffgasse 3a",
    "Türkenschanzstraße 17 (Sternwarte)",
    "Universitätsring 1",
    "Porzellangasse 4",
    "Postgasse 9",
    "Pramergasse 9",
    "Rathausstraße 19",
    "Rennweg 14",
    "Rooseveltplatz 2",
    "Universitätsstraße 7 (NIG)",
    "Währinger Gürtel 18-20",
    "Währinger Straße 10",
    "Währinger Straße 17",
    "Währinger Straße 17 (Kavalierstrakt)",
    "Währinger Straße 29",
    "Währinger Straße 38-42",
    "Schenkenstraße 4",
    "Schenkenstraße 8-10",
    "Schlossbergweg 18",
    "Schottenbastei 10-16 (Juridicum)",
    "Schottenbastei 7-9",
    "Schwarzspanierstraße 17",
    "Schönbrunner Straße 222-228",
    "Wiener Straße 38, 3100 St. Pölten",
]

BUILDING_ALIASES = {
    "hauptgebäude": "Universitätsring 1 (Hauptgebäude)",
    "juridicum": "Schottenbastei 10-16 (Juridicum)",
    "juridicum 5": "Schottenbastei 10-16 (Juridicum)",
    "juridicum 6": "Schottenbastei 10-16 (Juridicum)",
    "uza": "Althanstraße 14 (UZA I)",
    "uza i": "Althanstraße 14 (UZA I)",
    "uza ii": "UZA II",
    "uza2": "UZA II",
    "uza 2": "UZA II",
    "josef-holaubek-platz 2 (uza ii)": "UZA II",
    "nig": "Universitätsstraße 7 (NIG)",
    "nig 1": "Universitätsstraße 7 (NIG)",
    "nig 2": "Universitätsstraße 7 (NIG)",
    "nig 3": "Universitätsstraße 7 (NIG)",
    "nig 4": "Universitätsstraße 7 (NIG)",
    "nig 5": "Universitätsstraße 7 (NIG)",
    "nig 6": "Universitätsstraße 7 (NIG)",
    "nig 7": "Universitätsstraße 7 (NIG)",
    "nig 8": "Universitätsstraße 7 (NIG)",
    "ubb": "Djerassiplatz 1 (UBB)",
    "djerassiplatz 1": "Djerassiplatz 1 (UBB)",
    "sternwarte": "Türkenschanzstraße 17 (Sternwarte)",
    "türkenschanzstraße 17": "Türkenschanzstraße 17 (Sternwarte)",
    "kavalierstrakt": "Währinger Straße 17 (Kavalierstrakt)",
    "campus": "Spitalgasse 2",
    "spitalgasse 4": "Spitalgasse 4, Hof 10",
    "hirnforschungzentrum spitalgasse 4": "Spitalgasse 4, Hof 10",
    "fakultätszentrum für biodiversität": "Rennweg 14",
    "rennweg": "Rennweg 14",
    "online": "Digital",
    "unicampus zugang hof 2": "UniCampus Hof 2",
    "inst. f. orientalistik unicampus 1f": "Inst. f. Orientalistik UniCampus",
    "blindeninstitut wittelsbachstr. 5 1020": "Blindeninstitut Wittelsbachstr. 5",
    "schulschiff, 1210 wien": "Schulschiff, 1210 Wien, Donauinselplatz",
    "hanuschgasse": "Hanuschgasse 3",
}


def _lookup_official(key):
    """Exact alias/official lookup, with a retry that drops a trailing
    house-number-like token ("Oskar-Morgenstern-Platz 1 1" -> official)."""
    if key in BUILDING_ALIASES:
        return BUILDING_ALIASES[key]
    for name in OFFICIAL_BUILDINGS:
        if name.lower() == key:
            return name
    m = re.search(r"\s+\d+$", key)
    if m:
        base = key[: m.start()].strip()
        if base in BUILDING_ALIASES:
            return BUILDING_ALIASES[base]
        for name in OFFICIAL_BUILDINGS:
            if name.lower() == base:
                return name
    return None


def _split_addr_num(name):
    """Split 'Währinger Straße 38-42' -> (street_key, lo, hi), or None."""
    m = re.match(r"^(.+?)\s+(\d+)(?:\s*[-/]\s*(\d+))?", name)
    if not m:
        return None
    street = re.sub(r"[\s.\-]", "", m.group(1)).lower()
    lo = int(m.group(2))
    hi = int(m.group(3)) if m.group(3) else lo
    return street, lo, hi


OFFICIAL_ADDR = [
    (name, _split_addr_num(name)) for name in OFFICIAL_BUILDINGS
]
OFFICIAL_ADDR = [(n, s) for n, s in OFFICIAL_ADDR if s]


def canonicalize_building(b):
    """Map an extracted building name onto the official u:space designation."""
    if b == "?":
        return b
    key = re.sub(r"\s+", " ", b).strip().lower()
    hit = _lookup_official(key)
    if hit:
        return hit
    m = re.match(r"^(.+?)\s*(\d+)([a-zA-Z])?$", key)
    if m:
        street = re.sub(r"[\s.\-]", "", m.group(1)).lower()
        num = int(m.group(2))
        if m.group(3):  # letter suffix (e.g. "3a") — require exact match only
            return b
        cands = {
            name
            for name, (s, lo, hi) in OFFICIAL_ADDR
            if s == street and lo <= num <= hi
        }
        if len(cands) == 1:
            return cands.pop()
    return b
# Longest names first so "Währinger Gürtel" wins over a partial match.
BUILDINGS.sort(key=len, reverse=True)
# Optional trailing house number is captured so "Oskar-Morgenstern-Platz 1"
# and "Boltzmanngasse 5" stay distinct buildings.
BUILDING_RE = re.compile(
    r"\b(" + "|".join(re.escape(b) for b in BUILDINGS) + r")( \d+[a-zA-Z]?)?\b",
    re.IGNORECASE,
)
# Suffixes that leak floor/storey info into a building name.
FLOOR_SUFFIX_RE = re.compile(
    r"(\s+\d+\s*\.?\s*(OG|Stock|Stk|Stockwerk|Stiege|Stg)\b|\s+EG\b|\s+Hof\s+\S+|\s+Stiege\s+\S+|"
    r"\s+\(\s*Raum[^)]*\)|\s+Raum\s+\S+|\s+Aufgang\s+\S+|\s+\d{4}\s+Wien\s*$)+\s*$",
    re.IGNORECASE,
)
# Generic street addresses ("Philippovichgasse 11", "Dr.-Bohr-Gasse 9a",
# "Herrengasse 6-8") and UniCampus courtyards, used as a second-pass rule.
ADDRESS_RE = re.compile(
    r"([A-ZÄÖÜ][\wäöüß.\-]*(?:gasse|straße|strasse|platz|gürtel|weg)\s*\.?\s*\d+(?:\s*[-/]\s*\d+)?[a-zA-Z]?"
    r"|[Uu]ni[Cc]ampus\s+Hof\s+\d+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- downloads
def download(term, kind):
    RAW_DIR.mkdir(exist_ok=True)
    path = RAW_DIR / f"{kind}-{term}.ics"
    if path.exists() and path.stat().st_size > 1000:
        print(f"[skip] {path} already exists")
        return path
    url = f"{BASES[kind]}?query={urllib.parse.quote(f'{term} c:{CAP}')}"
    print(f"[dl ] {url}")
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "roomfinder-build/1.0"})
    with urllib.request.urlopen(req, timeout=900, context=_SSL_CONTEXT) as r:
        data = r.read()
    n = data.count(b"BEGIN:VEVENT")
    # Write raw bytes verbatim: text-mode write would translate "\n" -> "\r\n"
    # on Windows, corrupting an already-CRLF feed into "\r\r\n".
    path.write_bytes(data)
    if n == 0:
        raise RuntimeError(
            f"{url} returned 0 events ({len(data)} bytes). The endpoint silently "
            "returns an empty calendar above its c: cap or for unknown terms."
        )
    print(f"[ok ] {path}: {n} events, {len(data)/1e6:.1f} MB, {time.time()-t0:.0f}s")
    return path


# ------------------------------------------------------------------ parsing
def parse_file(path, src):
    """Return (rows, skipped) with rows = [course_id, group, kind, title,
    room, start, end, src, url]."""
    data = path.read_bytes()
    # Decode from raw bytes (not read_text) so Windows universal newlines can't
    # mangle an already-stray "\r\r\n" into a phantom blank line, then collapse
    # any run of CRs before a LF into one LF.
    text = data.decode("utf-8", "replace")
    text = re.sub(r"\r+\n", "\n", text).replace("\r", "\n")
    rows, skipped = [], 0
    for e in roomfind.parse_ics(text):
        ds, de = e.get("dtstart", ""), e.get("dtend", "")
        if len(ds) < 15 or len(de) < 15:  # not a full datetime; skip
            skipped += 1
            continue
        raw_title = e.get("title") or ""
        course_id, group, kind, title = (
            e.get("course_id"), e.get("group_no"), e.get("kind"), raw_title
        )
        if src == "exams" and not e.get("course_id"):
            m = EXAM_SUMMARY_RE.match(raw_title)
            if m:
                course_id, group, kind, title = m.group(1), m.group(2), "PRF", m.group(3)
        rows.append([
            course_id or "", group or "", kind or "", title or "",
            re.sub(r"\s*,\s*", ", ", e.get("room", "")
                   .replace("Ort in u:find Details", "").strip(" ,;")),
            roomfind.dt_to_sql(ds)[:16], roomfind.dt_to_sql(de)[:16],
            src, e.get("url", ""),
        ])
    return rows, skipped


# ----------------------------------------------------------- room catalog
# A comma-separated LOCATION can contain several rooms ("Hörsaal 4 Chemie HP
# Währinger Straße 42, Kleiner Hörsaal 3 Chemie Boltzmanngasse 1 HP"). A new
# segment starts whenever a part begins with a room keyword; anything else
# (floor, staircase, ...) belongs to the previous segment.
ROOM_START_RE = re.compile(
    r"^(?:Kleiner|Großer|Kleinster|Größter)?\s*"
    r"(?:Hörsaal|Seminarraum|PC-Raum|PC-Unterrichtsraum|Unterrichtsraum|Computerraum|Labor|Übungsraum|Besprechungsraum|"
    r"Saal|Raum|Hörsaaltrakt|Seminarzentrum|Bibliothek|Lesesaal|Praxisraum|Praxis|"
    r"Aula|Turnsaal|BZB/Seminarraum|ZSU\s*-\s*USZ|Physikalisches?\s+(?:Labor|Praktikum))",
    re.IGNORECASE,
)
# Floor/storey tokens that carry no useful meaning for room identification.
# The last alternative also catches truncated junk like "4.Stoc" / "3.Ebene".
FLOOR_TOKEN_RE = re.compile(
    r"\s*\b(?:EG|HP|UG\d*|\d+OG|(?:\d+\.)?OG|TOP\s*\d+|\d+\.\s*(?:Stock|Stiege|Stg|Stockwerk)|"
    r"\d+\.\s*[A-Za-zäöüß]{1,6})\b\s*",
    re.IGNORECASE,
)
# Generic bare designations that identify no room on their own.
GENERIC_DESIGNATIONS = {
    "Hörsaal", "Seminarraum", "Raum", "Saal", "Turnsaal", "Aula",
    "Ort in u:find Details", "Digital", "Online",
}


def normalize_building(b):
    """Strip floor/room/postal leakage from a building name."""
    b = re.sub(r"\s+", " ", b).strip(" ,;")
    b = FLOOR_SUFFIX_RE.sub("", b).strip(" ,;")
    return b or "?"


# UniCampus-style room codes ("2H-O1-03", "2F-EG-32") must survive floor-token
# stripping — protect them with placeholders first.
CODE_RE = re.compile(r"\b[0-9A-Z]{1,3}-[A-Z0-9]{1,3}-[0-9A-Z.]+\b", re.IGNORECASE)


def clean_designation(d):
    """Drop parenthesized details, API junk markers and floor tokens."""
    d = d.replace("Ort in u:find Details", " ")
    d = re.sub(r"\([^)]*\)", " ", d)
    codes = {}
    for i, code in enumerate(CODE_RE.findall(d)):
        codes[f"\x00{i}\x00"] = code
        d = d.replace(code, f"\x00{i}\x00")
    d = FLOOR_TOKEN_RE.sub(" ", d)
    for ph, code in codes.items():
        d = d.replace(ph, code)
    d = re.sub(r"[()]", " ", d)  # leftover unbalanced parens
    d = re.sub(r"\b\d{4}\s+Wien\b", " ", d)  # postal codes
    d = re.sub(r"\s{2,}", " ", d).strip(" .,;:\\")
    # leading virtual-session markers ("Digital, Hörsaal 3 …")
    d = re.sub(r"^(?:Digital|Online|ZOOM)\s*,?\s*", "", d, flags=re.IGNORECASE)
    return d or "?"


def iter_segments(raw):
    """Split a raw LOCATION into one or more room segments. Prefix parts
    (venue/keyword-less heads) are joined with commas preserved, so
    comma-containing building keywords still match downstream."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    prefix, segments = [], []
    for p in parts:
        if ROOM_START_RE.match(p):
            segments.append(", ".join([*prefix, p]))
            prefix = []
        elif segments:
            segments[-1] += ", " + p
        else:
            prefix.append(p)
    if prefix:
        segments.append(", ".join(prefix))
    return segments or [raw]


def _pair_from_segment(segment):
    """Extract (designation, building) from a single room segment."""
    segment = re.sub(r"\s+", " ", segment).strip()
    if not segment:
        return None

    # pure virtual sessions have no physical building
    if re.fullmatch(r"(?:Hybride Lehre|Virtueller \S+(?: \d+)?)", segment, re.IGNORECASE):
        return segment, "Digital"

    matches = list(BUILDING_RE.finditer(segment))
    # prefer a real building over virtual placeholders ("Digital, Hörsaal 1 …")
    m = next(
        (x for x in matches if x.group(0).strip().lower() not in ("digital", "online", "zoom")),
        matches[0] if matches else None,
    )
    if m:
        raw_building = m.group(0)
        # a building keyword may swallow the courtyard number the generic
        # address rule would have kept ("UniCampus Hof 2" -> "UniCampus")
        mh = re.match(r"\s+Hof\s+(\d+)\b", segment[m.end():])
        if mh:
            if raw_building.strip().lower() == "unicampus":
                raw_building = f"UniCampus Hof {mh.group(1)}"
            else:
                raw_building = raw_building + mh.group(0)
        # canonicalize on the RAW text — normalize_building would strip postal
        # tails ("Schulschiff, 1210 Wien" -> "Schulschiff") before the alias
        # lookup can see them
        building = canonicalize_building(raw_building)
        designation = segment[: m.start()]
        if not designation.strip(" ,;"):
            # segment starts with the building name — room info follows it
            designation = segment[m.end():]
    else:
        # second pass: any street address / UniCampus courtyard in the string
        # (keep as-is — FLOOR_SUFFIX_RE would wrongly strip "Hof N")
        m = ADDRESS_RE.search(segment)
        if m:
            building = canonicalize_building(re.sub(r"\s+", " ", m.group(0)).strip(" ,;"))
            designation = segment[: m.start()] + " " + segment[m.end():]
        else:
            # fallback: split on commas/semicolons; the first part that is not
            # floor/postal leakage is the building
            parts = [p.strip() for p in re.split(r",|;", segment) if p.strip()]
            if len(parts) > 1:
                def floorish(p):
                    return bool(re.match(r"^\d|^$|^(?:EG|HP|UG)\b", normalize_building(p), re.I))
                bpart = next((p for p in parts[1:] if not floorish(p)), None)
                if bpart is not None:
                    extras = " ".join(p for p in parts[1:] if p is not bpart)
                    return (clean_designation(parts[0] + " " + extras),
                            canonicalize_building(bpart))
                return (clean_designation(" ".join(parts)), "?")
            return clean_designation(segment), "?"

    designation = clean_designation(designation)
    if designation == "?":
        if building == "?":
            return None
        # venue-only strings ("Blindeninstitut Wittelsbachstr. 5, 1020 Wien"):
        # the venue name is the designation
        return building, building
    return designation, building


def split_location(raw):
    """Map a raw LOCATION string to all (designation, building) pairs it
    describes (multi-room sessions yield more than one)."""
    pairs = []
    for seg in iter_segments(raw):
        pair = _pair_from_segment(seg)
        if pair and pair not in pairs:
            pairs.append(pair)
    return pairs or [("?", "?")]

    m = BUILDING_RE.search(raw)
    if m:
        building = normalize_building(m.group(0))
        designation = raw[: m.start()].strip(" ,;")
        return (designation or "?"), building

    # second pass: any street address / UniCampus courtyard in the string
    # (keep as-is — FLOOR_SUFFIX_RE would wrongly strip "Hof N")
    m = ADDRESS_RE.search(raw)
    if m:
        building = re.sub(r"\s+", " ", m.group(0)).strip(" ,;")
        designation = (raw[: m.start()] + " " + raw[m.end():]).strip(" ,;")
        return (designation or "?"), building

    # fallback: split on commas/semicolons; second part is the building
    parts = [p.strip() for p in re.split(r",|;", raw) if p.strip()]
    if len(parts) > 1:
        building = normalize_building(parts[1])
        return parts[0], building
    return raw, "?"


def build_catalog(all_rows):
    """Group raw LOCATION strings into (designation, building) pairs. A raw
    multi-room string is registered under every pair it describes.

    The source data truncates long multi-room LOCATION strings mid-word
    ("..., Kleiner Hörsaal 3 Ch"). When a truncated segment is a strict prefix
    of exactly one longer segment seen elsewhere, it is repaired to that
    segment's pair so no junk entries enter the catalog."""
    seg_pair = {}  # segment string -> (designation, building)
    seg_raws = {}  # segment string -> {raw: count}
    seg_order = {}  # raw -> ordered segments
    for row in all_rows:
        raw = row[FIELDS.index("room")]
        if not raw:
            continue
        seg_order[raw] = segs = iter_segments(raw)
        for seg in segs:
            seg_raws.setdefault(seg, {})
            seg_raws[seg][raw] = seg_raws[seg].get(raw, 0) + 1
            if seg not in seg_pair:
                seg_pair[seg] = _pair_from_segment(seg)

    # repair truncated segments (iterate until stable for chained truncations).
    # A segment is truncation, not a distinct room, when exactly one longer
    # segment extends it, the extension does not start with a digit (else
    # "Hörsaal 1" would swallow "Hörsaal 10"), and the buildings are
    # prefix-compatible (covers both "?" and half-truncated building names).
    changed = True
    while changed:
        changed = False
        for seg in list(seg_pair):
            pair = seg_pair[seg]
            if pair is None:
                continue
            d, b = pair
            targets = set()
            for t, tp in seg_pair.items():
                if t == seg or tp is None or not t.startswith(seg):
                    continue
                nxt = t[len(seg):len(seg) + 1]
                if nxt.isdigit():
                    continue
                tb = tp[1]
                if b == "?" or tb == "?" or tb.startswith(b) or b.startswith(tb):
                    targets.add(tp)
            targets.discard(pair)
            targets.discard(None)
            if len(targets) == 1 and (b == "?" or next(iter(targets))[1] != b):
                seg_pair[seg] = next(iter(targets))
                changed = True

    catalog = {}  # (designation, building) -> {raw: count}
    for raw, segs in seg_order.items():
        prev_b = None
        for seg in segs:
            count = seg_raws[seg][raw]
            pair = seg_pair[seg]
            if pair is not None:
                d, b = pair
            else:
                d, b = clean_designation(seg), "?"
            # comma-continued rooms without an own building inherit the
            # previous segment's venue ("… Hauptgebäude, …, Seminarraum 8")
            if b == "?" and prev_b:
                b = prev_b
            # generic designations are only useful together with a building
            if d == "?" or (d in GENERIC_DESIGNATIONS and b == "?"):
                if b != "?":
                    prev_b = b
                continue
            catalog.setdefault((d, b), {})
            catalog[(d, b)][raw] = catalog[(d, b)].get(raw, 0) + count
            if b != "?":
                prev_b = b
    return catalog


# ------------------------------------------------------------------- output
def dedupe_rows(rows):
    """Collapse group-duplicated sessions: some courses list their shared
    lecture once per parallel group (same start/end/room/course, different
    group). Same room + time + course (and source) is one physical event, so
    keep a single copy.

    Only real, located, time-boxed events are collapsed. Room-less or
    zero-duration rows (e.g. full-day exams without a fixed slot) carry no
    "one physical occupancy" guarantee — several distinct exams for the same
    course all share an empty room and a 00:00-00:00 slot — so they are left
    untouched."""
    i = {k: FIELDS.index(k) for k in ("room", "start", "end", "course_id", "src")}
    seen, out = set(), []
    for row in rows:
        start, end, room = row[i["start"]], row[i["end"]], row[i["room"]]
        if not room or start == end:
            out.append(row)
            continue
        key = (start, end, room, row[i["course_id"]], row[i["src"]])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _room_pairs_map(rows):
    """Cache raw LOCATION string -> frozenset of (designation, building) pairs
    via split_location, for overlap testing."""
    i_room = FIELDS.index("room")
    cache = {}
    for row in rows:
        raw = row[i_room]
        if raw in cache:
            continue
        try:
            cache[raw] = frozenset(split_location(raw))
        except Exception:
            cache[raw] = frozenset()
    return cache


def merge_overlapping(rows):
    """Collapse per-group copies of a shared course session. When a lecture is
    split across parallel groups, u:find lists it once per group — all sharing
    (start, end, course_id, src) and the main room, but each appending that
    group's own extra room(s) (e.g. "Hörsaal 1 …, PC-Unterrichtsraum 3 …").
    Merge such rows into a single event whose room is the union of every
    group's rooms (so no room loses its schedule) and whose group label is the
    combined list. Groups are keyed on identical course id — distinct courses
    are never merged."""
    i_room = FIELDS.index("room")
    i_start = FIELDS.index("start")
    i_end = FIELDS.index("end")
    i_course = FIELDS.index("course_id")
    i_src = FIELDS.index("src")
    i_group = FIELDS.index("group")
    room_pairs = _room_pairs_map(rows)

    groups = {}
    for row in rows:
        key = (row[i_start], row[i_end], row[i_course], row[i_src])
        groups.setdefault(key, []).append(row)

    out = []
    for grp in groups.values():
        if len(grp) == 1:
            out.append(grp[0])
            continue
        # connected clusters: merge rows whose room sets overlap
        clusters = []
        for row in grp:
            pairs = room_pairs[row[i_room]]
            for cl in clusters:
                if pairs & cl["pairs"]:
                    cl["rows"].append(row)
                    cl["pairs"] |= pairs
                    break
            else:
                clusters.append({"rows": [row], "pairs": pairs})
        for cl in clusters:
            if len(cl["rows"]) == 1:
                out.append(cl["rows"][0])
                continue
            base = list(cl["rows"][0])
            segs, glist = [], []
            for row in cl["rows"]:
                for seg in iter_segments(row[i_room]):
                    if seg not in segs:
                        segs.append(seg)
                if row[i_group] and row[i_group] not in glist:
                    glist.append(row[i_group])
            base[i_room] = ", ".join(segs)
            base[i_group] = ",".join(glist)
            out.append(base)
    return out


_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                          "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def _slug(name):
    """Filesystem-safe ASCII slug (ä->ae, ü->ue, …) for shard filenames."""
    return re.sub(r"[^A-Za-z0-9]+", "-", name.translate(_UMLAUT)).strip("-").lower()


def write_shards(per_term_rows, catalog, top_n=17):
    """Write per-building event shards for the top-N busiest buildings plus one
    combined shard for every other building, so picking a building fetches a
    small file instead of the whole semester. N=17 keeps the "rest" shard under
    ~5 MB (measured on 2026S/2026W); room-less rows stay out (no building to
    match a selection). Returns (shard_index, shards_rest) for rooms.json."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    i_room = FIELDS.index("room")
    # raw LOCATION string -> set of buildings it describes (a multi-room string
    # maps to several buildings and lands in each of their shards)
    raw2b = {}
    for (_d, b), variants in catalog.items():
        for raw in variants:
            raw2b.setdefault(raw, set()).add(b)
    counts = {}
    for rows in per_term_rows.values():
        for row in rows:
            for b in raw2b.get(row[i_room], ("?",)):
                counts[b] = counts.get(b, 0) + 1
    top = [b for b, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
           if b != "?"][:top_n]
    top_set = set(top)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    shard_index = {}
    shards_rest = {}
    for term, rows in per_term_rows.items():
        for b in top:
            slug = _slug(b)
            fname = f"events-{term}-{slug}.json"
            matches = [r for r in rows if b in raw2b.get(r[i_room], ())]
            payload = {
                "generated": now, "term": term, "building": b,
                "fields": FIELDS, "rows": matches,
            }
            (OUT_DIR / fname).write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            shard_index.setdefault(b, {})[term] = fname
            print(f"[shard] {fname}: {len(matches)} events ({b})")
        # combined shard for every non-top building a row touches
        rest = [r for r in rows if raw2b.get(r[i_room], set()) - top_set]
        fname = f"events-{term}-rest.json"
        payload = {
            "generated": now, "term": term, "building": "(other buildings)",
            "fields": FIELDS, "rows": rest,
        }
        (OUT_DIR / fname).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        shards_rest[term] = fname
        print(f"[shard] {fname}: {len(rest)} events (all other buildings)")
    return shard_index, shards_rest


def write_outputs(per_term_rows, catalog, shard_index=None, shards_rest=None):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for term, rows in per_term_rows.items():
        payload = {
            "generated": now,
            "term": term,
            "fields": FIELDS,
            "rows": rows,
        }
        out = OUT_DIR / f"events-{term}.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(f"[out] {out}: {len(rows)} events")

    rooms = [
        {"d": d, "b": b, "raw": sorted(variants)}
        for (d, b), variants in sorted(
            catalog.items(), key=lambda kv: (kv[0][1], kv[0][0])
        )
    ]
    buildings = sorted({r["b"] for r in rooms})
    rooms_payload = {
        "generated": now,
        "terms": sorted(per_term_rows),
        "buildings": buildings,
        "rooms": rooms,
        "shards": shard_index or {},
        "shardsRest": shards_rest or {},
    }
    out = OUT_DIR / "rooms.json"
    out.write_text(
        json.dumps(rooms_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"[out] {out}: {len(rooms)} room entries, {len(buildings)} buildings")
    return rooms


def write_review(catalog, per_term_rows):
    counts = {}
    for rows in per_term_rows.values():
        for row in rows:
            raw = row[FIELDS.index("room")]
            counts[raw] = counts.get(raw, 0) + 1
    flagged = sum(1 for (d, b) in catalog if b == "?")
    lines = [
        "# Room/building mapping — review pass",
        "",
        f"**{len(catalog)} (designation, building) pairs, {flagged} flagged with building `?`.**",
        "Check that designation and building look right; fix any `?` entries and",
        "mis-assignments by editing `BUILDINGS` / `split_location()` in build_data.py.",
        "",
        "| sessions | designation | building | raw LOCATION variants |",
        "|---:|---|---|---|",
    ]
    for (d, b), variants in sorted(
        catalog.items(), key=lambda kv: (kv[0][1], kv[0][0])
    ):
        n = sum(counts.get(v, 0) for v in variants)
        raws = "<br>".join(
            v.replace("|", "\\|") for v in sorted(variants)
        )
        lines.append(f"| {n} | {d} | **{b}** | {raws} |")
    Path("rooms_review.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[out] rooms_review.md ({flagged} entries flagged for review)")


def main():
    no_download = "--no-download" in sys.argv
    fresh = "--fresh" in sys.argv or "--redownload" in sys.argv
    per_term_rows = {}
    all_rows = []
    for term in TERMS:
        rows = []
        for kind in BASES:
            path = RAW_DIR / f"{kind}-{term}.ics"
            if fresh and path.exists():
                path.unlink()          # force a fresh download
            if not path.exists():
                if no_download:
                    print(f"[warn] missing {path}, skipping ({term}/{kind})")
                    continue
                path = download(term, kind)
            r, skipped = parse_file(path, kind)
            print(f"[parse] {path.name}: {len(r)} events ({skipped} skipped)")
            rows.extend(r)
        per_term_rows[term] = rows

    # collapse group-duplicated sessions (a shared lecture listed once per
    # parallel group) before cataloguing and sharding — first exact duplicates,
    # then overlaps (identical course/time, differing group-appended rooms)
    for term in per_term_rows:
        per_term_rows[term] = dedupe_rows(per_term_rows[term])
        per_term_rows[term] = merge_overlapping(per_term_rows[term])
    all_rows = [r for rows in per_term_rows.values() for r in rows]

    catalog = build_catalog(all_rows)
    shard_index, shards_rest = write_shards(per_term_rows, catalog)
    rooms = write_outputs(per_term_rows, catalog, shard_index, shards_rest)
    write_review(catalog, per_term_rows)
    flagged = sum(1 for r in rooms if r["b"] == "?")
    print(f"\nDone. {flagged} room entries flagged with building '?' — see rooms_review.md")


if __name__ == "__main__":
    main()
