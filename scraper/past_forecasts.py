"""Past day-ahead forecasts for the sites' weather stations (NBM station guidance, "NBS", from the IEM archive).

The model should learn from what was *forecast*, not what happened, because that's what it sees when it scores
tomorrow at 6 PM. For each station and day D we keep the 19Z run issued on D-1 (about 3 PM Eastern, the last run
before the 6 PM briefing) and summarize its hours that fall on D (station local time):

  fc_max_temp_f, fc_min_temp_f, fc_max_heat_index_f, fc_heat_hours_103 (hours with heat index >= 103 F, the crew
  heat-risk measure), fc_qpf_in (rain amount), fc_max_pop (chance of rain %), fc_max_thunder (3-h thunder chance %),
  fc_max_gust_kt, fc_max_wind_kt

Run from the project root (slow the first time, then resumable):  python scraper/past_forecasts.py
"""
import csv
import datetime as dt
import io
import json
import math
import time
from collections import defaultdict
from zoneinfo import ZoneInfo

from scrape_power_plants import DATA, RAW, get

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
RUN_HOUR = "19"          # 19Z run on the day before
START = dt.date(2021, 10, 1)
OUT = DATA / "nbs_daily_forecasts.csv"
CACHE = RAW / "nbs_daily"
FIELDS = ["fc_max_temp_f", "fc_min_temp_f", "fc_max_heat_index_f", "fc_heat_hours_103", "fc_qpf_in", "fc_max_pop",
          "fc_max_thunder", "fc_max_gust_kt", "fc_max_wind_kt"]


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def heat_index_f(t, td):
    """NWS heat index (Rothfusz regression with the standard adjustments) from temperature and dew point, in F."""
    if t is None or td is None:
        return None
    rh = 100 * math.exp(17.625 * ((td - 32) / 1.8) / (243.04 + (td - 32) / 1.8)) / \
        math.exp(17.625 * ((t - 32) / 1.8) / (243.04 + (t - 32) / 1.8))
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        return round(simple, 1)
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 0.00683783 * t * t
          - 0.05481717 * rh * rh + 0.00122874 * t * t * rh + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return round(hi, 1)


def summarize(rows, tz):
    """19Z-run rows of one station -> {local target day: features} (only hours on the day after the run)."""
    days = defaultdict(list)
    for r in rows:
        if r["runtime"][11:13] != RUN_HOUR:
            continue
        run_day = dt.date.fromisoformat(r["runtime"][:10])
        ft = dt.datetime.fromisoformat(r["ftime"]).replace(tzinfo=dt.timezone.utc).astimezone(tz)
        if ft.date() == run_day + dt.timedelta(days=1):
            days[ft.date()].append(r)
    out = {}
    for day, rs in days.items():
        t = [num(r["tmp"]) for r in rs if num(r["tmp"]) is not None]
        hi = [heat_index_f(num(r["tmp"]), num(r["dpt"])) for r in rs]
        hi = [h for h in hi if h is not None]
        mx = lambda k: max((num(r[k]) for r in rs if num(r[k]) is not None), default=None)
        out[day] = {
            "fc_max_temp_f": max(t, default=None), "fc_min_temp_f": min(t, default=None),
            "fc_max_heat_index_f": max(hi, default=None), "fc_heat_hours_103": 3 * sum(h >= 103 for h in hi),
            "fc_qpf_in": round(sum(num(r["q06"]) or 0 for r in rs) / 100, 2),  # NBM QPF is in hundredths of an inch
            "fc_max_pop": mx("p06"), "fc_max_thunder": mx("t03"), "fc_max_gust_kt": mx("gst"), "fc_max_wind_kt": mx("wsp"),
        }
    return out


def station_year(sid, tz, year):
    path = CACHE / f"{sid}_{year}.csv"
    this_year = year == dt.date.today().year
    if path.exists() and not this_year:  # past years never change; the current year is refreshed
        return list(csv.DictReader(open(path)))
    sts = max(dt.date(year, 1, 1), START) - dt.timedelta(days=1)
    ets = min(dt.date(year + 1, 1, 1), dt.date.today())
    url = f"{IEM}?station=K{sid}&model=NBS&sts={sts}T00:00Z&ets={ets}T00:00Z&format=csv"
    days = summarize(csv.DictReader(io.StringIO(get(url).decode())), tz)
    rows = [{"station": sid, "day": d.isoformat(), **f} for d, f in sorted(days.items()) if d.year == year]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, ["station", "day", *FIELDS])
        w.writeheader()
        w.writerows(rows)
    time.sleep(2)  # be polite to IEM
    return rows


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    stations = sorted({r["station"] for r in csv.DictReader(open(DATA / "plant_stations.csv", encoding="utf-8"))})
    tz = {}
    for p in RAW.glob("iem_*_ASOS.geojson"):
        for f in json.loads(p.read_text())["features"]:
            tz[f["properties"]["sid"]] = f["properties"].get("tzname") or "America/New_York"
    rows = []
    for n, sid in enumerate(stations, 1):
        for year in range(START.year, dt.date.today().year + 1):
            try:
                rows += station_year(sid, ZoneInfo(tz.get(sid, "America/New_York")), year)
            except Exception as e:  # one bad station-year shouldn't stop the rest
                print(f"  {sid} {year}: {e}")
        print(f"  {n}/{len(stations)} {sid}: {len(rows)} station-days so far", flush=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, ["station", "day", *FIELDS])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} station-days to {OUT}")


if __name__ == "__main__":
    main()
