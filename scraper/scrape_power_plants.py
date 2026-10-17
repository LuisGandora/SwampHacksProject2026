"""Scrape power plant locations for US states into data/power_plants.csv.

Sources:
  EIA-860M monthly generator inventory: plant ID, name, operator, county, lat/lon, capacity (primary)
  OpenStreetMap via Overpass (power=plant): cross-checks coordinates, fills missing ones
  FCC Area API: county + county FIPS from coordinates
  timezonefinder (offline): IANA time zone from coordinates, for the 6 PM local briefing
"""
import argparse
import csv
import datetime
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import openpyxl
from timezonefinder import TimezoneFinder

DATA = Path(__file__).resolve().parent.parent / "data"
RAW = DATA / "raw"
UA = {"User-Agent": "SwampHacks2026-powerplant-scraper/1.0"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
OVERPASS = "https://overpass-api.de/api/interpreter"
MATCH_KM = 2.0   # nearest-OSM fallback radius when no ref:US:EIA tag
REVIEW_KM = 1.0  # EIA vs OSM distance above this gets flagged for manual review


def get(url, data=None, headers=UA):
    """GET (or POST with data), retrying when a public server is busy or rate limiting (429/502/503/504)."""
    req = urllib.request.Request(url, data=data, headers=headers)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if attempt == 4 or e.code not in (429, 502, 503, 504):
                raise
            wait = int(e.headers.get("Retry-After", "0") or 0) if str(e.headers.get("Retry-After", "")).isdigit() else 0
            wait = max(wait, 30 * (attempt + 1))
            print(f"  {urllib.parse.urlsplit(url).netloc} returned {e.code}, retrying in {wait}s...")
            time.sleep(wait)


def download_eia860m():
    """Newest EIA-860M file, searching back 7 months (current month lives in xls/, older in archive/xls/)."""
    today = datetime.date.today()
    for back in range(7):
        y, m = divmod(today.year * 12 + today.month - 1 - back, 12)
        name = f"{MONTHS[m]}_generator{y}.xlsx"
        path = RAW / name
        if path.exists():
            return path
        for sub in ("xls", "archive/xls"):
            try:
                body = get(f"https://www.eia.gov/electricity/data/eia860m/{sub}/{name}")
            except urllib.error.HTTPError:
                continue
            if body[:2] == b"PK":  # missing months redirect to an HTML page
                path.write_bytes(body)
                return path
    raise SystemExit("No EIA-860M file found for the last 7 months")


def load_eia_plants(xlsx, states):
    rows = openpyxl.load_workbook(xlsx, read_only=True)["Operating"].iter_rows(min_row=3, values_only=True)
    h = {k: i for i, k in enumerate(next(rows))}
    plants, tech = {}, defaultdict(lambda: defaultdict(float))
    for r in rows:
        state = r[h["Plant State"]]
        if state not in states:
            continue
        pid = r[h["Plant ID"]]
        mw = r[h["Nameplate Capacity (MW)"]]
        mw = mw if isinstance(mw, (int, float)) else 0.0
        lat, lon = r[h["Latitude"]], r[h["Longitude"]]
        p = plants.setdefault(pid, {
            "eia_plant_id": pid, "name": r[h["Plant Name"]], "operator": r[h["Entity Name"]],
            "state": state, "eia_county": r[h["County"]],
            "lat": lat if isinstance(lat, (int, float)) else None,
            "lon": lon if isinstance(lon, (int, float)) else None,
            "coord_source": "eia860m", "capacity_mw": 0.0, "generators": 0, "fuels": set(),
        })
        p["capacity_mw"] += mw
        p["generators"] += 1
        p["fuels"].add(r[h["Energy Source Code"]])
        tech[pid][r[h["Technology"]]] += mw
    for pid, p in plants.items():
        p["primary_technology"] = max(tech[pid], key=tech[pid].get)
        p["fuels"] = ";".join(sorted(f for f in p["fuels"] if f))
        p["capacity_mw"] = round(p["capacity_mw"], 1)
    return list(plants.values())


def fetch_osm(state):
    q = f'[out:json][timeout:180];area["ISO3166-2"="US-{state}"]->.a;nwr["power"="plant"](area.a);out center tags;'
    els = json.loads(get(OVERPASS, urllib.parse.urlencode({"data": q}).encode()))["elements"]
    return [{"osm_id": f"{e['type']}/{e['id']}",
             "lat": e.get("lat", e.get("center", {}).get("lat")),
             "lon": e.get("lon", e.get("center", {}).get("lon")),
             "tags": e.get("tags", {})} for e in els]


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def match_osm(plants, osm):
    """Attach OSM match per plant: by ref:US:EIA tag first, else nearest within MATCH_KM. Fills missing coords."""
    by_ref = {ref.strip(): o for o in osm for ref in o["tags"].get("ref:US:EIA", "").split(";") if ref.strip()}
    for p in plants:
        o, how = by_ref.get(str(p["eia_plant_id"])), "eia_ref"
        if o and p["lat"] is None:
            p["lat"], p["lon"], p["coord_source"] = o["lat"], o["lon"], "osm"
        if not o and p["lat"] is not None and osm:
            o, how = min(osm, key=lambda x: km(p["lat"], p["lon"], x["lat"], x["lon"])), "nearest"
            if km(p["lat"], p["lon"], o["lat"], o["lon"]) > MATCH_KM:
                o = None
        if not o:
            p.update(osm_id="", osm_name="", osm_match="none", osm_distance_km="", coord_check="no_osm")
            continue
        d = round(km(p["lat"], p["lon"], o["lat"], o["lon"]), 3)
        p.update(osm_id=o["osm_id"], osm_name=o["tags"].get("name", ""), osm_match=how,
                 osm_distance_km=d, coord_check="ok" if d <= REVIEW_KM else "review")


def add_county_and_tz(plants):
    tf = TimezoneFinder()
    cache_path = RAW / "fcc_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    try:
        for n, p in enumerate(plants, 1):
            p["county"] = p["county_fips"] = p["timezone"] = ""
            if p["lat"] is None:
                continue
            p["timezone"] = tf.timezone_at(lat=p["lat"], lng=p["lon"]) or ""
            key = f"{p['lat']:.5f},{p['lon']:.5f}"
            if key not in cache:
                try:
                    res = json.loads(get(f"https://geo.fcc.gov/api/census/area?lat={p['lat']}&lon={p['lon']}&format=json"))["results"]
                except (urllib.error.URLError, TimeoutError, ValueError) as e:
                    print(f"  FCC lookup failed for plant {p['eia_plant_id']}: {e}")  # retried next run (not cached)
                    continue
                cache[key] = res[0] if res else {}
                time.sleep(0.1)
            p["county"] = cache[key].get("county_name", "")
            p["county_fips"] = cache[key].get("county_fips", "")
            if n % 100 == 0:
                print(f"  county/tz: {n}/{len(plants)}")
    finally:
        cache_path.write_text(json.dumps(cache))
    norm = lambda s: (s or "").lower().replace(" county", "").replace(".", "").replace("-", " ").strip()
    for p in plants:
        p["county_matches_eia"] = norm(p["county"]) == norm(p["eia_county"]) if p["county"] else ""


COLUMNS = ["state", "eia_plant_id", "name", "operator", "primary_technology", "fuels", "capacity_mw", "generators",
           "lat", "lon", "coord_source", "county", "county_fips", "eia_county", "county_matches_eia", "timezone",
           "osm_id", "osm_name", "osm_match", "osm_distance_km", "coord_check"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states", nargs="+", default=["FL", "GA", "AL"], help="2-letter state codes (default: FL GA AL)")
    ap.add_argument("--top", type=int, default=0, help="keep only the N largest plants per state by capacity (default: all)")
    ap.add_argument("--out", type=Path, default=DATA / "power_plants.csv")
    args = ap.parse_args()
    states = [s.upper() for s in args.states]
    if not all(len(s) == 2 and s.isalpha() for s in states):
        ap.error("--states must be 2-letter codes, e.g. FL GA AL")
    RAW.mkdir(parents=True, exist_ok=True)

    xlsx = download_eia860m()
    print(f"EIA-860M: {xlsx.name}")
    plants = load_eia_plants(xlsx, set(states))

    out = []
    for s in states:
        sp = sorted((p for p in plants if p["state"] == s), key=lambda p: -p["capacity_mw"])
        if args.top:
            sp = sp[:args.top]
        print(f"{s}: {len(sp)} EIA plants; querying OSM...")
        match_osm(sp, fetch_osm(s))
        time.sleep(2)  # be polite to the shared Overpass server
        out += sp

    print("Looking up county (FCC) and time zone...")
    add_county_and_tz(out)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    print(f"\nWrote {len(out)} plants to {args.out}")
    for s in states:
        sp = [p for p in out if p["state"] == s]
        c = lambda k, v: sum(p[k] == v for p in sp)
        print(f"{s}: {len(sp):4d} plants | OSM by EIA ref {c('osm_match', 'eia_ref')}, nearest {c('osm_match', 'nearest')}, "
              f"none {c('osm_match', 'none')} | coords to review {c('coord_check', 'review')} | "
              f"county mismatches {c('county_matches_eia', False)}" + ("  <-- fewer than 50!" if len(sp) < 50 else ""))


if __name__ == "__main__":
    main()
