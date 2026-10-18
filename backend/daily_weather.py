"""Keep weather_daily current: each morning, fetch the last 7 days of IEM daily summaries for the sites'
stations and upsert them. The hazard model's "rain over the previous 6 days" input and any retraining read
weather_daily, so it must not stop at the day the history was first loaded. One request per state network.
"""
import csv
import datetime as dt
import io
import time

import httpx

client = httpx.Client(timeout=120, headers={"User-Agent": "PlanIT-Control/0.1 (SwampHacks 2026)"})


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def get(url):
    for attempt in range(4):  # IEM answers 429/503 when busy
        r = client.get(url)
        if r.status_code in (429, 503) and attempt < 3:
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.content


IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"
FIELDS = {"max_temp_f": "max_temp_f", "min_temp_f": "min_temp_f", "max_heat_index_f": "max_feel",
          "max_dewpoint_f": "max_dewpoint_f", "min_rh": "min_rh", "avg_rh": "avg_rh", "precip_in": "precip_in",
          "max_gust_kt": "max_wind_gust_kts", "max_wind_kt": "max_wind_speed_kts"}
# Some ASOS stations (EUF, BCR, ...) report "sustained wind" of 200-959 kt for days with no gust: sensor errors. Real
# sustained wind can't beat the gust at the same station, so over 87 kt (100 mph) without a gust at least as high is blanked.
BAD_WIND = "UPDATE weather_daily SET max_wind_kt = NULL WHERE max_wind_kt > 87 AND (max_gust_kt IS NULL OR max_gust_kt < max_wind_kt)"


def sync(conn, days=7):
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    nets = conn.execute("SELECT network, array_agg(station_id) AS ids FROM weather_stations GROUP BY network").fetchall()
    rows = []
    for n, net in enumerate(nets):
        if n:
            time.sleep(10)  # IEM blocks rapid requests
        q = "&".join(f"stations={s}" for s in sorted(net["ids"]))
        url = (f"{IEM}?network={net['network']}&{q}&year1={start.year}&month1={start.month}&day1={start.day}"
               f"&year2={end.year}&month2={end.month}&day2={end.day}&format=csv&na=blank")
        for r in csv.DictReader(io.StringIO(get(url).decode())):
            rows.append((r["station"], r["day"], *(num(r[src]) for src in FIELDS.values())))
    cols = ", ".join(FIELDS)
    conn.cursor().executemany(
        f"INSERT INTO weather_daily (station_id, day, {cols}) VALUES (%s, %s, {', '.join(['%s'] * len(FIELDS))}) "
        f"ON CONFLICT (station_id, day) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in FIELDS), rows)
    conn.execute(BAD_WIND + " AND day >= %s", (start,))
    return f"{len(rows)} station-days upserted for {start} to {end}"
