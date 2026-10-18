"""Real crew dispatches from utilities' public outage maps -> crew_dispatch and crew_trips.

Many US utilities publish their outage map on KUBRA StormCenter. Each outage on it carries the utility's own status
text ("Personnel have been assigned...", "Personnel are currently on site repairing... The cause of the outage is a
dead tree"). Polling every 15 minutes turns that into a crew log: when a crew was assigned, when it reached the
site, when power came back (the outage leaves the map), and the cause. Only outages within RADIUS_MI of one of our
sites are kept. Each finished outage that had a crew becomes a crew_trips row (source 'outage_map'), which
ml/economic_impact.py correlates with weather like company trip logs.

To add a utility anywhere in the US: open its outage map, find `instanceId` and `viewId` in the page source, and
add a MAPS row. These endpoints are public but undocumented: they can change, so a failing map is skipped and
reported, never fatal. Polite use: one pass per map per run, MAX_TILES requests at most.
"""
import datetime as dt
import math
import re

import httpx

MAPS = [  # utility, KUBRA instanceId, viewId, (south, west, north, east) service area to scan
    ("Alabama Power", "7636a60f-7b81-4fb0-a30d-ed79a8e271e7", "c3471c92-6e1b-494b-a884-391139a2cc18", (30.1, -88.5, 35.1, -84.9)),
    ("Georgia Power", "7b38c047-7950-444b-a25c-9b3e5ab986eb", "67b44af5-3847-4ca3-9f4e-9190aac343d6", (30.3, -85.6, 35.1, -80.8)),
    ("Mississippi Power", "32c5f878-d103-49e1-8a8a-ad0e2bdb4507", "1521bb54-d36b-4d36-a64d-48a454ee39a0", (30.1, -89.9, 33.0, -88.0)),
    ("JEA", "2bb4315d-ff9d-4937-a231-57b8a9df189c", "40a074f2-b303-42b7-b717-ed7d9d2ad9e2", (30.0, -82.1, 30.6, -81.3)),
]
API = "https://kubra.io/stormcenter/api/v1/stormcenters/{i}/views/{v}"
RADIUS_MI = 25
MAX_TILES = 400       # per map per run; storms can mean thousands of outages, so stop early rather than hammer it
GONE_AFTER = dt.timedelta(minutes=45)   # an outage missing from the map this long is restored
client = httpx.Client(timeout=20, headers={"User-Agent": "PlanetControl/0.1 (SwampHacks 2026)"})

ASSIGNED = re.compile(r"assigned|dispatched|en route|on the way|crew is responding", re.I)
ON_SITE = re.compile(r"on site|on-site|onsite|repairing|making repairs|working to repair|in the area to (?:evaluate|repair)"
                     r"|working", re.I)
CAUSE = re.compile(r"cause of the outage is (?:an? )?([^.]+)\.", re.I)


def quadkey(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return "".join(str((x >> (i - 1) & 1) + 2 * (y >> (i - 1) & 1)) for i in range(z, 0, -1))


def polyline_point(s):
    """First point of a Google-encoded polyline (KUBRA's outage geometry) as (lat, lon)."""
    out, i = [], 0
    for _ in range(2):
        shift = value = 0
        while True:
            b = ord(s[i]) - 63
            i += 1
            value |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        out.append((~value >> 1 if value & 1 else value >> 1) / 1e5)
    return out[0], out[1]


def english(v):
    """Some maps (JEA) send text per language, {"EN-US": "...", "es-US": "...", "orig": "WORKING"}: keep English + code."""
    if isinstance(v, dict):
        return " ".join(str(x) for x in (v.get("EN-US") or next(iter(v.values()), None), v.get("orig")) if x)
    return v


def status(desc):
    """(assigned, on_site, cause) from the outage's crew_status field and the utility's status text."""
    text = " ".join(str(english(desc.get(k)) or "") for k in ("crew_status", "comments"))
    cause = desc.get("cause")
    cause = (english(cause.get("EN-US") if isinstance(cause, dict) else cause)
             or (m.group(1).strip() if (m := CAUSE.search(text)) else None))
    on_site = bool(ON_SITE.search(text))
    return on_site or bool(ASSIGNED.search(text)) or bool(desc.get("crew_icon")), on_site, cause


def outages(instance, view, box):
    """Every individual outage on one KUBRA map inside box: [(lat, lon, customers, desc)]."""
    base = API.format(i=instance, v=view)
    state = client.get(f"{base}/currentState?preview=false").json()
    conf = client.get(f"{base}/configuration/{state['stormcenterDeploymentId']}?preview=false").text
    layer = min(set(re.findall(r"cluster-\d+", conf)))
    url = "https://kubra.io/" + state["data"]["cluster_interval_generation_data"] + f"/public/{layer}/" + "{q}.json"
    s, w, n, e = box
    tiles = list({quadkey(la / 10, lo / 10, 7) for la in range(int(s * 10), int(n * 10) + 1, 3)
                  for lo in range(int(w * 10), int(e * 10) + 1, 3)})
    found, calls = [], 0
    while tiles and calls < MAX_TILES:
        q = tiles.pop()
        calls += 1
        r = client.get(url.replace("{qkh}", q[-3:][::-1]).format(q=q))
        if r.status_code != 200:
            continue
        data = r.json().get("file_data") or []
        if any(x["desc"].get("cluster") for x in data) and len(q) < 16:   # zoom into clusters
            tiles += [q + d for d in "0123"]
            continue
        for x in data:
            if not x["desc"].get("cluster") and x.get("geom", {}).get("p"):
                lat, lon = polyline_point(x["geom"]["p"][0])
                found.append((lat, lon, (x["desc"].get("cust_a") or {}).get("val"), x["desc"]))
    return found, calls


UPSERT = """
WITH o AS (SELECT %(utility)s AS utility, ST_MakePoint(%(lon)s, %(lat)s)::geography AS geog),
near AS (SELECT s.site_id, ST_Distance(s.geog, o.geog) / 1609.344 AS miles FROM sites s, o
          WHERE ST_DWithin(s.geog, o.geog, %(radius)s * 1609.344) ORDER BY s.geog <-> o.geog LIMIT 1),
open AS (SELECT incident_id FROM crew_dispatch, o WHERE crew_dispatch.utility = o.utility AND cleared_at IS NULL
          AND ST_DWithin(crew_dispatch.geog, o.geog, 30) LIMIT 1),
upd AS (UPDATE crew_dispatch SET last_seen = now(), customers_max = greatest(customers_max, %(cust)s),
               assigned_at = coalesce(assigned_at, CASE WHEN %(assigned)s THEN now() END),
               on_site_at = coalesce(on_site_at, CASE WHEN %(on_site)s THEN now() END),
               cause = coalesce(%(cause)s, cause), last_comment = %(comment)s
         WHERE incident_id = (SELECT incident_id FROM open) RETURNING 1)
INSERT INTO crew_dispatch (utility, geog, site_id, site_miles, first_seen, last_seen, assigned_at, on_site_at,
                           customers_max, cause, last_comment)
SELECT o.utility, o.geog, near.site_id, round(near.miles::numeric, 1), now(), now(),
       CASE WHEN %(assigned)s THEN now() END, CASE WHEN %(on_site)s THEN now() END, %(cust)s, %(cause)s, %(comment)s
  FROM o JOIN near ON true
 WHERE NOT EXISTS (SELECT 1 FROM upd) AND NOT EXISTS (SELECT 1 FROM open)
"""
CLOSE = """
WITH done AS (UPDATE crew_dispatch SET cleared_at = last_seen
               WHERE cleared_at IS NULL AND last_seen < now() - %s AND utility = ANY (%s) RETURNING *)
INSERT INTO crew_trips (trip_id, site_id, started_at, ended_at, purpose, geog, source)
SELECT 'outage_map:' || incident_id, site_id, coalesce(on_site_at, assigned_at), cleared_at,
       'outage repair (' || utility || ')' || coalesce(': ' || cause, ''), geog, 'outage_map'
  FROM done WHERE coalesce(on_site_at, assigned_at) IS NOT NULL
ON CONFLICT (trip_id) DO NOTHING
"""


def sync(conn):
    seen, errors, polled = 0, [], []
    for utility, instance, view, box in MAPS:
        try:
            found, calls = outages(instance, view, box)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            errors.append(f"{utility}: {type(e).__name__}")
            continue
        for lat, lon, cust, desc in found:
            assigned, on_site, cause = status(desc)
            conn.execute(UPSERT, {"utility": utility, "lat": lat, "lon": lon, "radius": RADIUS_MI, "cust": cust,
                                  "assigned": assigned, "on_site": on_site, "cause": cause and cause[:120],
                                  "comment": str(english(desc.get("comments")) or english(desc.get("crew_status")) or "")[:300] or None})
        seen += len(found)
        polled.append(utility)
    trips = conn.execute(CLOSE, (GONE_AFTER, polled)).rowcount  # a map that failed can't close its outages
    return f"{seen} outages on {len(MAPS) - len(errors)} maps, {trips} crew trips closed" + (f"; failed: {errors}" if errors else "")
