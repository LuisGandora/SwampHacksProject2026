"""Thinned power plants for the rest of the lower 48 -> data/us_live_sites.csv (live data only).

    python scraper/us_live_sites.py           # then: python -m db.load_live_sites

The full Southeast study area (FL/GA/AL, data/power_plants.csv) keeps every plant with its history and the Hazard
Index model. Everywhere else in the contiguous US gets the 2 largest plants (by capacity) in each ~50 x 50 mile
square: about 2,000 sites with live alerts, forecasts, readings, lightning and rivers, but no history and no model
score (the model was trained on Southeast weather and locations). Coordinates are EIA-860M's, not cross-checked with
OpenStreetMap. Each site gets its nearest active ASOS weather station from the Iowa Environmental Mesonet.
"""
import csv
import json
import math
from collections import defaultdict

from scrape_power_plants import DATA, RAW, add_county_and_tz, download_eia860m, get, km, load_eia_plants

FULL = {"FL", "GA", "AL"}   # already loaded with full history
LOWER_48 = ("AL AZ AR CA CO CT DE FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
            "OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
CELL_MI = 50
PER_CELL = 2
COLUMNS = ["state", "eia_plant_id", "name", "operator", "primary_technology", "fuels", "capacity_mw", "generators",
           "lat", "lon", "county", "county_fips", "eia_county", "timezone", "station", "station_name",
           "station_distance_mi", "nws_office"]


def thin(plants):
    """Keep the PER_CELL largest plants in each CELL_MI x CELL_MI square (longitude width adjusted for latitude)."""
    cells = defaultdict(list)
    for p in plants:
        if p["lat"] is None or p["lon"] is None:
            continue
        row = math.floor(p["lat"] * 69.0 / CELL_MI)
        width = CELL_MI / (69.17 * math.cos(math.radians((row + 0.5) * CELL_MI / 69.0)))
        cells[(row, math.floor(p["lon"] / width))].append(p)
    return [p for ps in cells.values() for p in sorted(ps, key=lambda p: -p["capacity_mw"])[:PER_CELL]]


def stations(state):
    """Active ASOS stations in a state (IEM network file, cached in data/raw)."""
    path = RAW / f"iem_{state}_ASOS.geojson"
    if not path.exists():
        path.write_bytes(get(f"https://mesonet.agron.iastate.edu/geojson/network/{state}_ASOS.geojson"))
    return [f for f in json.loads(path.read_text())["features"]
            if not f["properties"].get("archive_end") and f.get("geometry")]


def main():
    plants = thin(load_eia_plants(download_eia860m(), set(LOWER_48) - FULL))
    print(f"{len(plants)} plants after thinning to {PER_CELL} per {CELL_MI}-mile square")
    near = {}
    for st in sorted({p["state"] for p in plants} | {"AL", "FL", "GA"}):  # neighbours' stations can be closer
        near[st] = stations(st)
    pool = [f for fs in near.values() for f in fs]
    for p in plants:
        best = min(pool, key=lambda f: km(p["lat"], p["lon"], f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]))
        p["station"] = best["properties"]["sid"]
        p["station_name"] = best["properties"]["sname"]
        p["nws_office"] = best["properties"].get("wfo")
        p["station_distance_mi"] = round(km(p["lat"], p["lon"], best["geometry"]["coordinates"][1],
                                            best["geometry"]["coordinates"][0]) / 1.609344, 1)
        p["_station_feature"] = best
    print("Looking up county (FCC) and time zone...")
    add_county_and_tz(plants)
    with open(DATA / "us_live_sites.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(p for p in plants if p["timezone"])
    used = {p["station"]: p["_station_feature"] for p in plants}
    (DATA / "us_live_stations.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": list(used.values())}))
    print(f"Wrote {sum(1 for p in plants if p['timezone'])} sites and {len(used)} stations")


if __name__ == "__main__":
    main()
