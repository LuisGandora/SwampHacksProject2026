"""Relate power plants to weather casualties within X miles.

Inputs:
  data/power_plants.csv            from scrape_power_plants.py
  NOAA Storm Events (details)      events with deaths or injuries (downloaded)
  NWS zone-county file             places zone-based storm events, which have no coordinates (downloaded)
  OSHA Severe Injury Reports       weather-related reports: environmental heat/cold, lightning (downloaded)
  CDC WONDER export (optional)     weather deaths by county; WONDER's API can't return county data, so export it by hand

Outputs:
  data/plant_weather_events.csv    one row per (plant, nearby casualty event)
  data/plant_weather_summary.csv   one row per plant with totals
"""
import argparse
import csv
import datetime
import gzip
import io
import math
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from scrape_power_plants import DATA, RAW, get, km

# osha.gov's CDN rejects anything that doesn't look like a browser
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
NOAA = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
OSHA_PAGE = "https://www.osha.gov/severe-injury-reports"
NWS_ZONES_PAGE = "https://www.weather.gov/gis/ZoneCounty"
OSHA_WEATHER = ("environmental heat", "environmental cold", "temperature extremes", "lightning")
KM_PER_MILE = 1.609344


def cached(name, url, **kw):
    path = RAW / name
    if not path.exists():
        print(f"  downloading {name}")
        path.write_bytes(get(url, **kw))
    return path


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load_zones():
    """{(state, zone): (lat, lon, {fips of counties lying wholly in the zone})} from the newest NWS zone-county file."""
    name = re.findall(r"bp\d\d[a-z]{2}\d\d\.dbx", get(NWS_ZONES_PAGE).decode("latin-1"))[-1]  # page lists oldest first
    zones, state_of_fips, zones_of_county = {}, {}, defaultdict(set)
    for line in cached(name, f"https://www.weather.gov/source/gis/Shapefiles/County/{name}").read_text("latin-1").splitlines():
        f = line.split("|")
        if len(f) < 11:
            continue
        state, zone, fips, lat, lon = f[0], f[1], f[6], num(f[9]), num(f[10])
        zones.setdefault((state, zone), (lat, lon, set()))[2].add(fips)
        zones_of_county[fips].add((state, zone))
        state_of_fips[fips[:2]] = state
    # a county split into zones (e.g. Coastal vs Inland Palm Beach) can't say which zone a plant is in
    for z in zones.values():
        z[2].difference_update(f for f in list(z[2]) if len(zones_of_county[f]) > 1)
    return zones, state_of_fips


def storm_events(start_year, zones, state_of_fips):
    """NOAA events with any deaths or injuries. Point events use begin/end coords, zone events the zone centroid."""
    listing = get(NOAA).decode()
    events, unlocated = [], 0
    for year in range(start_year, datetime.date.today().year + 1):
        names = sorted(set(re.findall(rf"StormEvents_details-ftp_v1\.0_d{year}_c\d+\.csv\.gz", listing)))
        if not names:
            continue
        with gzip.open(cached(names[-1], NOAA + names[-1]), "rt", encoding="latin-1") as f:
            for r in csv.DictReader(f):
                deaths = int(r["DEATHS_DIRECT"] or 0) + int(r["DEATHS_INDIRECT"] or 0)
                injuries = int(r["INJURIES_DIRECT"] or 0) + int(r["INJURIES_INDIRECT"] or 0)
                if not deaths and not injuries:
                    continue
                points = [(num(r[a]), num(r[b])) for a, b in (("BEGIN_LAT", "BEGIN_LON"), ("END_LAT", "END_LON"))]
                points, fips, how = [p for p in points if None not in p], set(), "point"
                if not points and r["CZ_TYPE"] == "Z":
                    z = zones.get((state_of_fips.get(r["STATE_FIPS"].zfill(2)), r["CZ_FIPS"].zfill(3)))
                    if z:
                        points, fips, how = [z[:2]], z[2], "zone"
                if not points:
                    unlocated += 1  # marine zones, county events without coords, retired zone IDs
                    continue
                ym = r["BEGIN_YEARMONTH"]
                events.append({
                    "source": "noaa_storm_events", "event_id": r["EVENT_ID"],
                    "date": f"{ym[:4]}-{ym[4:]}-{int(r['BEGIN_DAY']):02d}", "event_type": r["EVENT_TYPE"],
                    "deaths": deaths, "injuries": injuries, "place": f"{r['CZ_NAME'].title()}, {r['STATE'].title()}",
                    "details": r["EVENT_NARRATIVE"] or r["EPISODE_NARRATIVE"], "points": points, "fips": fips, "how": how,
                })
    print(f"  NOAA: {len(events)} casualty events since {start_year} ({unlocated} could not be placed)")
    return events


def osha_rows():
    """Every OSHA Severe Injury Report, from the newest full-dataset zip linked on OSHA's page."""
    page = get(OSHA_PAGE, headers=BROWSER).decode("utf-8", "replace")
    href = re.search(r"/sites/default/files/[^\"']+\.zip", page).group(0)
    z = zipfile.ZipFile(cached(href.rsplit("/", 1)[1], "https://www.osha.gov" + href, headers=BROWSER))
    with z.open(z.namelist()[0]) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"))


def osha_events(start_year):
    events = []
    for r in osha_rows():
        lat, lon = num(r["Latitude"]), num(r["Longitude"])
        if lat is None or lon is None or not any(w in r["EventTitle"].lower() for w in OSHA_WEATHER):
            continue
        date = datetime.datetime.strptime(r["EventDate"], "%m/%d/%Y").date()
        if date.year < start_year:
            continue
        events.append({
            "source": "osha_severe_injury", "event_id": r["ID"], "date": date.isoformat(),
            "event_type": r["EventTitle"].strip(), "deaths": 0, "injuries": 1,  # one report = one injured worker
            "place": f"{r['Employer']}, {r['City'].title()}, {r['State'].title()}",
            "details": r["Final Narrative"], "points": [(lat, lon)], "fips": set(), "how": "point",
        })
    print(f"  OSHA: {len(events)} weather-related severe injury reports since {start_year}")
    return events


def cdc_wonder(path):
    """{county fips: (deaths, suppressed rows)} from a tab-delimited CDC WONDER export grouped by County."""
    out = defaultdict(lambda: [0, 0])
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            fips = (r.get("County Code") or "").strip()
            if not fips.isdigit():
                continue  # notes/footer rows
            d = num(r.get("Deaths"))
            if d is None:
                out[fips.zfill(5)][1] += 1  # "Suppressed" (fewer than 10 deaths) or "Unreliable"
            else:
                out[fips.zfill(5)][0] += int(d)
    return out


def relate(plants, events, miles):
    """Yield (plant, event, distance_mi, match) for events within `miles`, or zone events covering the plant's county."""
    lim = miles * KM_PER_MILE
    for p in plants:
        lat, lon = float(p["lat"]), float(p["lon"])
        dlat, dlon = lim / 111, lim / (111 * math.cos(math.radians(lat)))  # cheap box test before haversine
        for e in events:
            near = [pt for pt in e["points"] if abs(pt[0] - lat) <= dlat and abs(pt[1] - lon) <= dlon]
            in_zone = p["county_fips"] in e["fips"]
            if not near and not in_zone:
                continue
            d = min(km(lat, lon, *pt) for pt in e["points"])
            if d <= lim or in_zone:
                yield p, e, round(d / KM_PER_MILE, 1), e["how"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--miles", type=float, default=25, help="radius around each plant (default: 25)")
    ap.add_argument("--start-year", type=int, default=2015, help="earliest event year (default: 2015, when OSHA data begins)")
    ap.add_argument("--plants", type=Path, default=DATA / "power_plants.csv")
    ap.add_argument("--cdc", type=Path, default=DATA / "cdc_wonder_weather_deaths.txt", help="CDC WONDER export (see README)")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    plants = [p for p in csv.DictReader(open(args.plants, encoding="utf-8")) if p["lat"]]
    print(f"{len(plants)} plants, {args.miles:g}-mile radius")
    zones, state_of_fips = load_zones()
    events = storm_events(args.start_year, zones, state_of_fips) + osha_events(args.start_year)
    cdc = cdc_wonder(args.cdc) if args.cdc.exists() else {}
    if not cdc:
        print(f"  CDC WONDER: no export at {args.cdc}, skipping (see README)")

    summary = {p["eia_plant_id"]: {"storm_events": 0, "storm_deaths": 0, "storm_injuries": 0, "types": Counter(),
                                   "osha_weather_reports": 0} for p in plants}
    with open(DATA / "plant_weather_events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["plant_id", "plant_name", "plant_state", "plant_county", "source", "event_id", "date", "event_type",
                    "deaths", "injuries", "distance_mi", "match", "place", "details"])
        n = 0
        for p, e, d, how in relate(plants, events, args.miles):
            w.writerow([p["eia_plant_id"], p["name"], p["state"], p["county"], e["source"], e["event_id"], e["date"],
                        e["event_type"], e["deaths"], e["injuries"], d, how, e["place"], e["details"]])
            s, n = summary[p["eia_plant_id"]], n + 1
            if e["source"] == "osha_severe_injury":
                s["osha_weather_reports"] += 1
            else:
                s["storm_events"] += 1
                s["storm_deaths"] += e["deaths"]
                s["storm_injuries"] += e["injuries"]
                s["types"][e["event_type"]] += e["deaths"] + e["injuries"]

    with open(DATA / "plant_weather_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["plant_id", "plant_name", "state", "county", "county_fips", "capacity_mw", "primary_technology",
                    "lat", "lon", "timezone", "storm_casualty_events", "storm_deaths", "storm_injuries",
                    "top_storm_types", "osha_weather_reports", "cdc_county_weather_deaths", "cdc_suppressed_rows"])
        for p in plants:
            s, c = summary[p["eia_plant_id"]], cdc.get(p["county_fips"], ["", ""])
            w.writerow([p["eia_plant_id"], p["name"], p["state"], p["county"], p["county_fips"], p["capacity_mw"],
                        p["primary_technology"], p["lat"], p["lon"], p["timezone"], s["storm_events"], s["storm_deaths"],
                        s["storm_injuries"], "; ".join(t for t, _ in s["types"].most_common(3)),
                        s["osha_weather_reports"], *(c if cdc else ["", ""])])

    print(f"\nWrote {n} plant-event pairs to {DATA / 'plant_weather_events.csv'}")
    print(f"Wrote {len(plants)} plants to {DATA / 'plant_weather_summary.csv'}")


if __name__ == "__main__":
    main()
