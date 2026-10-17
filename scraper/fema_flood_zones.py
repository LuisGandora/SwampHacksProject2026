"""FEMA flood zone for every site, from the National Flood Hazard Layer (FEMA Flood Map Service Center; free, no key).

Each site's coordinates are looked up in NFHL layer 28 (Flood Hazard Zones):
  zone A/AE/AH/AO/V/VE = inside the Special Flood Hazard Area (SFHA: the 1%-a-year, "100-year" floodplain);
  V/VE also has coastal wave action; X (shaded) = 0.2%-a-year floodplain; X = minimal hazard;
  no map polygon = FEMA hasn't mapped that spot digitally ("unmapped").
Writes data/site_flood_zones.csv; db/setup_db.py loads it onto the sites table. Run from the project root:
    python scraper/fema_flood_zones.py
"""
import csv
import json
import time
import urllib.error
import urllib.parse

from scrape_power_plants import DATA, get

NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
RISK = ["VE", "V", "AE", "AH", "AO", "A99", "A", "X", "D"]   # worst first, when a site touches several zones


def zone_at(lat, lon):
    q = urllib.parse.urlencode({"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                                "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false", "f": "json",
                                "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DFIRM_ID"})
    for attempt in range(5):  # FEMA's server drops connections now and then
        try:
            feats = [f["attributes"] for f in json.loads(get(f"{NFHL}?{q}"))["features"]]
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError, KeyError):
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    if not feats:
        return None
    rank = lambda a: (a["SFHA_TF"] != "T", RISK.index(a["FLD_ZONE"]) if a["FLD_ZONE"] in RISK else len(RISK))
    return min(feats, key=rank)


def main():
    plants = [p for p in csv.DictReader(open(DATA / "power_plants.csv", encoding="utf-8")) if p["lat"]]
    out = []
    for i, p in enumerate(plants, 1):
        a = zone_at(float(p["lat"]), float(p["lon"]))
        bfe = a and a["STATIC_BFE"] not in (None, -9999) and a["STATIC_BFE"]
        out.append({"site_id": p["eia_plant_id"], "fld_zone": a["FLD_ZONE"] if a else "unmapped",
                    "zone_subtype": (a or {}).get("ZONE_SUBTY") or "", "sfha": bool(a and a["SFHA_TF"] == "T"),
                    "bfe_ft": bfe or "", "panel": (a or {}).get("DFIRM_ID") or ""})
        if i % 100 == 0:
            print(f"  {i}/{len(plants)}", flush=True)
        time.sleep(0.1)
    with open(DATA / "site_flood_zones.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} sites: {sum(r['sfha'] for r in out)} in a Special Flood Hazard Area, "
          f"{sum(r['fld_zone'] == 'unmapped' for r in out)} unmapped -> data/site_flood_zones.csv")


if __name__ == "__main__":
    main()
