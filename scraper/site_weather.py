"""Daily weather history for every plant, from the nearest NWS airport (ASOS) station.

The NWS climate pages (FSU Climate Center, NWS Melbourne, Tampa Bay, Miami) publish these same stations as PDFs
and text bulletins. The Iowa Environmental Mesonet (IEM) serves the data as CSV, including the daily max heat index.

Outputs:
  data/plant_stations.csv         plant -> nearest station, distance, NWS office, Florida climate region,
                                  and average days per year over heat / rain / wind thresholds
  data/station_weather_daily.csv  one row per station per day
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import time
from collections import defaultdict
from pathlib import Path

from relate_weather import KM_PER_MILE, cached, num
from scrape_power_plants import DATA, RAW, get, km

IEM = "https://mesonet.agron.iastate.edu"
# NWS office -> the Florida climate page that covers it
FL_REGION = {"TAE": "North/Panhandle", "JAX": "North/Panhandle", "MOB": "North/Panhandle",
             "MLB": "East Central", "TBW": "West Central", "MFL": "South Florida", "KEY": "South Florida"}
FIELDS = ["max_temp_f", "min_temp_f", "max_feel", "max_dewpoint_f", "min_rh", "avg_rh", "precip_in",
          "max_wind_gust_kts", "max_wind_speed_kts"]
HEAT_F, RAIN_IN, GUST_KT = 105, 2.0, 40  # NWS heat advisory level; heavy rain day; damaging gust territory


def stations(states, start):
    """Full NWS/FAA ASOS stations (those with 1-minute data) reporting since `start`, per state network.

    The networks also hold smaller AWOS sites; in FL/GA/AL most of them report rain on under a third of
    days, and their dew points run high enough to double or triple the heat-index days of nearby airports.
    """
    out = []
    for st in states:
        path = cached(f"iem_{st}_ASOS.geojson", f"{IEM}/geojson/network/{st}_ASOS.geojson")
        for f in json.loads(path.read_text())["features"]:
            p = f["properties"]
            if (p["online"] and (p.get("archive_begin") or "9999") <= start
                    and (p.get("attributes") or {}).get("HAS1MIN") == "1"):
                lon, lat = f["geometry"]["coordinates"]
                out.append({"sid": p["sid"], "name": p["sname"], "network": p["network"], "wfo": p.get("wfo") or "",
                            "lat": lat, "lon": lon})
    return out


def requests_for(stns, start, end):
    """(cache name, URL) per state network per calendar year, each covering all of that network's stations."""
    by_net = defaultdict(list)
    for s in stns:
        by_net[s["network"]].append(s["sid"])
    for net, sids in sorted(by_net.items()):
        tag = hashlib.md5(" ".join(sorted(sids)).encode()).hexdigest()[:8]  # new station set -> new cache file
        q = "&".join(f"stations={s}" for s in sorted(sids))
        for y in range(start.year, end.year + 1):
            a, b = max(start, datetime.date(y, 1, 1)), min(end, datetime.date(y, 12, 31))
            yield (f"iem_daily_{net}_{a}_{b}_{tag}.csv",
                   f"{IEM}/cgi-bin/request/daily.py?network={net}&{q}&year1={a.year}&month1={a.month}&day1={a.day}"
                   f"&year2={b.year}&month2={b.month}&day2={b.day}&format=csv&na=blank")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2015-01-01", help="first day (default: 2015-01-01)")
    ap.add_argument("--urls", type=Path, help="only write the download URLs to this file (to fetch by hand)")
    args = ap.parse_args()
    start, end = datetime.date.fromisoformat(args.start), datetime.date.today() - datetime.timedelta(days=1)
    RAW.mkdir(parents=True, exist_ok=True)

    plants = [p for p in csv.DictReader(open(DATA / "power_plants.csv", encoding="utf-8")) if p["lat"]]
    stns = stations(sorted({p["state"] for p in plants}), args.start)
    for p in plants:
        lat, lon = float(p["lat"]), float(p["lon"])
        p["stn"] = min(stns, key=lambda s: km(lat, lon, s["lat"], s["lon"]))
        p["stn_mi"] = round(km(lat, lon, p["stn"]["lat"], p["stn"]["lon"]) / KM_PER_MILE, 1)

    used = {p["stn"]["sid"]: p["stn"] for p in plants}
    reqs = list(requests_for(used.values(), start, end))
    if args.urls:
        args.urls.write_text("\n".join(url for _, url in reqs) + "\n")
        print(f"Wrote {len(reqs)} URLs to {args.urls}")
        return
    print(f"{len(plants)} plants -> {len(used)} stations; {len(reqs)} downloads for {start} to {end}...")
    # sid -> {field: [days with a value, days over threshold]}; a blank gust means no gust, so it counts every day
    days = defaultdict(lambda: {k: [0, 0] for k in ("all", "max_feel", "precip_in", "max_wind_gust_kts")})
    with open(DATA / "station_weather_daily.csv", "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["station", "date"] + FIELDS)
        for n, (name, url) in enumerate(reqs, 1):
            if not (RAW / name).exists():
                time.sleep(10)  # IEM blocks rapid requests
            for r in csv.DictReader(io.StringIO(cached(name, url).read_text())):
                w.writerow([r["station"], r["day"]] + [r[k] for k in FIELDS])
                d = days[r["station"]]
                d["all"][0] += 1
                for k, limit in (("max_feel", HEAT_F), ("precip_in", RAIN_IN), ("max_wind_gust_kts", GUST_KT)):
                    v = num(r[k])
                    d[k][0] += v is not None or k == "max_wind_gust_kts"
                    d[k][1] += (v or 0) >= limit
            print(f"  {n}/{len(reqs)} {name}", flush=True)

    with open(DATA / "plant_stations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["plant_id", "plant_name", "state", "station", "station_name", "station_distance_mi", "nws_office",
                    "fl_climate_region", "days_of_data", f"heat_index_{HEAT_F}f_days_per_year",
                    f"rain_{RAIN_IN:g}in_days_per_year", f"gust_{GUST_KT}kt_days_per_year"])
        for p in plants:
            s, d = p["stn"], days[p["stn"]["sid"]]
            per_year = lambda k: round(d[k][1] * 365.25 / d[k][0], 1) if d[k][0] else ""
            w.writerow([p["eia_plant_id"], p["name"], p["state"], s["sid"], s["name"], p["stn_mi"], s["wfo"],
                        FL_REGION.get(s["wfo"], "") if p["state"] == "FL" else "", d["all"][0], per_year("max_feel"), per_year("precip_in"),
                        per_year("max_wind_gust_kts")])
    print(f"Wrote {DATA / 'station_weather_daily.csv'} and {DATA / 'plant_stations.csv'}")


if __name__ == "__main__":
    main()
