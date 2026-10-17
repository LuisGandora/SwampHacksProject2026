"""Turn OSHA Severe Injury Reports near the power plants into seed crew hazard reports.

Categories (one report can fall in several):
  ppe              narrative says PPE / fall protection wasn't worn (basis: narrative), or the injury is one PPE
                   exists to prevent: a fall to a lower level or a lost eye (basis: injury_type)
  heavy_machinery  injury source is construction/excavating machinery, cranes/lifts/conveyors or forklifts and other
                   industrial vehicles (basis: source), or a pedestrian struck by a vehicle off-road (basis: event)
  slip_trip        same-level fall from a slip or trip; basis 'narrative' when a spill, debris, trash or cord is named
  osha_inspection  OSHA opened an inspection; cited violations need the DOL enforcement API (key required)

Output: data/crew_reports.csv, one row per (report, category).
"""
import argparse
import csv
import datetime
import math
import re

from relate_weather import KM_PER_MILE, num, osha_rows
from scrape_power_plants import DATA, km

PPE_TEXT = re.compile(
    r"\b(not|without|no|failed to|did not|didn't)\s+(wear(ing)?|use|using|have|having)?\s*"
    r"(a |his |her |their |any |the |proper |required )?(ppe|personal protective|hard ?hat|helmet|"
    r"safety (glasses|harness|goggles)|harness|fall (protection|arrest)|gloves|face shield|respirator|"
    r"lanyard|eye protection|hearing protection)\b|\bnot tied off\b", re.I)
WALKWAY_TEXT = re.compile(r"\b(spill(ed|s)?|wet floor|puddle|oil|grease|debris|trash|garbage|clutter|cords?|hoses?)\b", re.I)
MACHINERY_SOURCES = ("32", "34", "86")  # OIICS: construction/mining machinery, material handling, industrial vehicles


def categorize(r):
    """[(category, basis)] for one OSHA report row."""
    text, event, out = r["Final Narrative"], r["EventTitle"].lower(), []
    if PPE_TEXT.search(text):
        out.append(("ppe", "narrative"))
    elif "lower level" in event or (num(r["Loss of Eye"]) or 0) > 0:
        out.append(("ppe", "injury_type"))
    if r["Source"].strip()[:2] in MACHINERY_SOURCES:
        out.append(("heavy_machinery", "source"))
    elif "pedestrian" in event and "nonroadway" in event:
        out.append(("heavy_machinery", "event"))
    if re.search(r"same level.*(slip|trip)", event):
        out.append(("slip_trip", "narrative" if WALKWAY_TEXT.search(text) else "injury_type"))
    if r["Inspection"].strip():
        out.append(("osha_inspection", "inspection_opened"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--miles", type=float, default=50, help="keep reports within this distance of a plant (default: 50)")
    args = ap.parse_args()
    plants = [p for p in csv.DictReader(open(DATA / "power_plants.csv", encoding="utf-8")) if p["lat"]]
    for p in plants:
        p["lat"], p["lon"] = float(p["lat"]), float(p["lon"])
    pad = args.miles * KM_PER_MILE / 111
    box = (min(p["lat"] for p in plants) - pad, max(p["lat"] for p in plants) + pad,
           min(p["lon"] for p in plants) - pad * 1.3, max(p["lon"] for p in plants) + pad * 1.3)

    rows, seen = [], 0
    for r in osha_rows():
        lat, lon = num(r["Latitude"]), num(r["Longitude"])
        if lat is None or lon is None or not (box[0] <= lat <= box[1] and box[2] <= lon <= box[3]):
            continue
        cats = categorize(r)
        if not cats:
            continue
        c = math.cos(math.radians(lat))  # flat-earth distance is fine for picking the nearest plant
        p = min(plants, key=lambda p: (p["lat"] - lat) ** 2 + ((p["lon"] - lon) * c) ** 2)
        miles = km(lat, lon, p["lat"], p["lon"]) / KM_PER_MILE
        if miles > args.miles:
            continue
        seen += 1
        for cat, basis in cats:
            rows.append([r["ID"], datetime.datetime.strptime(r["EventDate"], "%m/%d/%Y").date().isoformat(), cat, basis,
                         r["Employer"], r["City"].title(), r["State"].title(), lat, lon, r["Primary NAICS"],
                         r["EventTitle"].strip(), r["SourceTitle"].strip(), r["Part of Body Title"].strip(),
                         int(num(r["Hospitalized"]) or 0), int(num(r["Amputation"]) or 0), int(num(r["Loss of Eye"]) or 0),
                         r["Inspection"].strip(), p["eia_plant_id"], p["name"], round(miles, 1), r["Final Narrative"]])

    with open(DATA / "crew_reports.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["report_id", "date", "category", "basis", "employer", "city", "state", "lat", "lon", "naics", "event",
                    "source", "body_part", "hospitalized", "amputation", "loss_of_eye", "osha_inspection",
                    "nearest_plant_id", "nearest_plant", "plant_distance_mi", "narrative"])
        w.writerows(rows)
    counts = {}
    for row in rows:
        counts[(row[2], row[3])] = counts.get((row[2], row[3]), 0) + 1
    print(f"Wrote {len(rows)} rows ({seen} reports within {args.miles:g} miles of a plant) to {DATA / 'crew_reports.csv'}")
    for (cat, basis), n in sorted(counts.items()):
        print(f"  {cat:16} {basis:18} {n}")


if __name__ == "__main__":
    main()
