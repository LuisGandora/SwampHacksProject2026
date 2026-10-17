"""Load history into Tiger Data and tie every site to its hazard events.

Loads (full reload, safe to re-run):
  storm_events            NOAA Storm Events details, mapped to hazard_categories via noaa_event_types
  storm_event_locations   NOAA extra points along those events
  storm_fatalities        NOAA one-row-per-person fatalities for those events
  osha_injuries           OSHA Severe Injury Reports, with crew and weather categories
  weather_fatalities      CDC WONDER county weather deaths (data/cdc_wonder_weather_deaths.txt)
  weather_daily           IEM daily station weather (data/station_weather_daily.csv)
Then runs refresh_site_hazard_events() and refreshes site_risk_profile.

Only events near the sites are stored (sites' bounding box plus about 70 miles), to stay well under the
free service's storage cap. Run db/setup_db.py first.
"""
import csv
import datetime
import gzip
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
from crew_reports import categorize  # noqa: E402
from relate_weather import NOAA, RAW, cached, get, load_zones, num, osha_rows  # noqa: E402
from setup_db import database_url  # noqa: E402

DATA = ROOT / "data"
PAD_DEG = 1.0  # bounding-box padding; covers the largest history radius (50 mi) with room to spare
OSHA_WEATHER = {"environmental heat": "extreme_heat", "environmental cold": "extreme_cold", "lightning": "lightning"}


def money(s):
    """NOAA damage like '10.00K', '1.5M', '2B' -> dollars."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMB]?)\s*", s or "", re.I)
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()] if m else None


def tz_of(cz_timezone):
    """'EST-5' -> UTC-5. Storm Events times are local standard time."""
    m = re.search(r"([+-]?\d+)$", cz_timezone or "")
    return datetime.timezone(datetime.timedelta(hours=int(m.group(1)) if m else 0))


def local_time(yearmonth, day, hhmm, tz):
    t = str(hhmm or "0").zfill(4)
    return datetime.datetime(int(yearmonth[:4]), int(yearmonth[4:]), int(day), int(t[:2]), int(t[2:]), tzinfo=tz)


def noaa_files(kind, years, listing):
    """Newest NOAA file of `kind` (details/fatalities/locations) for each year, downloaded once into data/raw."""
    out = {}
    for y in years:
        names = sorted(set(re.findall(rf"StormEvents_{kind}-ftp_v1\.0_d{y}_c\d+\.csv\.gz", listing)))
        if names:
            out[y] = cached(names[-1], NOAA + names[-1])
    return out


def rows_of(path):
    with gzip.open(path, "rt", encoding="latin-1") as f:
        yield from csv.DictReader(f)


def load_storms(conn, box, category_of):
    listing = get(NOAA).decode()
    years = sorted({int(m) for m in re.findall(r"StormEvents_details-ftp_v1\.0_d(\d{4})_c", listing) if int(m) >= 2015})
    zones, state_of_fips = load_zones()
    inside = lambda lat, lon: lat is not None and lon is not None and box[0] <= lat <= box[1] and box[2] <= lon <= box[3]
    kept, tz_of_event = 0, {}
    with conn.cursor().copy(
            "COPY storm_events (event_id, episode_id, begin_time, end_time, state, state_fips, event_type, category, "
            "cz_type, cz_fips, cz_name, wfo, injuries_direct, injuries_indirect, deaths_direct, deaths_indirect, "
            "damage_property_usd, damage_crops_usd, source, magnitude, magnitude_type, flood_cause, tropical_category, "
            "tor_f_scale, tor_length_mi, tor_width_yd, begin_location, end_location, geog, location_precision, "
            "episode_narrative, event_narrative) FROM STDIN") as cp:
        for year, path in noaa_files("details", years, listing).items():
            for r in rows_of(path):
                cat = category_of.get(r["EVENT_TYPE"])
                if not cat:
                    continue
                b = (num(r["BEGIN_LAT"]), num(r["BEGIN_LON"]))
                e = (num(r["END_LAT"]), num(r["END_LON"]))
                if inside(*b) and inside(*e) and b != e:
                    geog, how = f"SRID=4326;LINESTRING({b[1]} {b[0]},{e[1]} {e[0]})", "path"
                elif inside(*b) or inside(*e):
                    p = b if inside(*b) else e
                    geog, how = f"SRID=4326;POINT({p[1]} {p[0]})", "point"
                else:
                    z = zones.get((state_of_fips.get(r["STATE_FIPS"].zfill(2)), r["CZ_FIPS"].zfill(3))) \
                        if r["CZ_TYPE"] == "Z" and b[0] is None else None
                    if not z or not inside(z[0], z[1]):
                        continue  # outside the region, or no usable location
                    geog, how = f"SRID=4326;POINT({z[1]} {z[0]})", "zone"
                tz = tz_of(r["CZ_TIMEZONE"])
                tz_of_event[r["EVENT_ID"]] = tz
                cp.write_row((
                    r["EVENT_ID"], r["EPISODE_ID"] or None,
                    local_time(r["BEGIN_YEARMONTH"], r["BEGIN_DAY"], r["BEGIN_TIME"], tz),
                    local_time(r["END_YEARMONTH"], r["END_DAY"], r["END_TIME"], tz),
                    r["STATE"].title(), r["STATE_FIPS"].zfill(2), r["EVENT_TYPE"], cat,
                    r["CZ_TYPE"], r["CZ_FIPS"], r["CZ_NAME"].title(), r["WFO"],
                    int(r["INJURIES_DIRECT"] or 0), int(r["INJURIES_INDIRECT"] or 0),
                    int(r["DEATHS_DIRECT"] or 0), int(r["DEATHS_INDIRECT"] or 0),
                    money(r["DAMAGE_PROPERTY"]), money(r["DAMAGE_CROPS"]), r["SOURCE"] or None,
                    num(r["MAGNITUDE"]), r["MAGNITUDE_TYPE"] or None, r["FLOOD_CAUSE"] or None,
                    int(num(r["CATEGORY"])) if num(r["CATEGORY"]) is not None else None,
                    r["TOR_F_SCALE"] or None, num(r["TOR_LENGTH"]), num(r["TOR_WIDTH"]),
                    r["BEGIN_LOCATION"] or None, r["END_LOCATION"] or None, geog, how,
                    r["EPISODE_NARRATIVE"] or None, r["EVENT_NARRATIVE"] or None))
                kept += 1
    print(f"  storm_events: {kept}")

    n = 0
    with conn.cursor().copy("COPY storm_event_locations (event_id, episode_id, location_index, year_month, range_mi, "
                            "azimuth, location, geog) FROM STDIN") as cp:
        for path in noaa_files("locations", years, listing).values():
            for r in rows_of(path):
                lat, lon = num(r["LATITUDE"]), num(r["LONGITUDE"])
                if r["EVENT_ID"] in tz_of_event and lat is not None and lon is not None:
                    cp.write_row((r["EVENT_ID"], r["EPISODE_ID"] or None, r["LOCATION_INDEX"], r["YEARMONTH"],
                                  num(r["RANGE"]), r["AZIMUTH"] or None, r["LOCATION"] or None,
                                  f"SRID=4326;POINT({lon} {lat})"))
                    n += 1
    print(f"  storm_event_locations: {n}")

    n = 0
    with conn.cursor().copy("COPY storm_fatalities (fatality_id, event_id, fatality_time, is_direct, age, sex, location) "
                            "FROM STDIN") as cp:
        for path in noaa_files("fatalities", years, listing).values():
            for r in rows_of(path):
                tz = tz_of_event.get(r["EVENT_ID"])
                if tz is None:
                    continue
                cp.write_row((r["FATALITY_ID"], r["EVENT_ID"],
                              local_time(r["FAT_YEARMONTH"], r["FAT_DAY"], r["FAT_TIME"], tz),
                              r["FATALITY_TYPE"] == "D", int(num(r["FATALITY_AGE"])) if num(r["FATALITY_AGE"]) else None,
                              r["FATALITY_SEX"] or None, r["FATALITY_LOCATION"] or None))
                n += 1
    print(f"  storm_fatalities: {n}")


def load_osha(conn, box):
    n = 0
    with conn.cursor().copy(
            "COPY osha_injuries (report_id, event_time, employer, address, city, state, zip, naics, hospitalized, "
            "amputation, loss_of_eye, inspection_id, nature, part_of_body, event_code, event_title, source_code, "
            "source_title, narrative, crew_categories, weather_category, geog) FROM STDIN") as cp:
        for r in osha_rows():
            lat, lon = num(r["Latitude"]), num(r["Longitude"])
            if lat is None or lon is None or not (box[0] <= lat <= box[1] and box[2] <= lon <= box[3]):
                continue
            title = r["EventTitle"].strip()
            weather = next((c for k, c in OSHA_WEATHER.items() if k in title.lower()), None)
            day = datetime.datetime.strptime(r["EventDate"], "%m/%d/%Y").replace(tzinfo=datetime.timezone.utc)
            cp.write_row((r["ID"], day, r["Employer"], " ".join(filter(None, (r["Address1"], r["Address2"]))) or None,
                          r["City"].title(), r["State"].title(), r["Zip"] or None, r["Primary NAICS"] or None,
                          int(num(r["Hospitalized"]) or 0), int(num(r["Amputation"]) or 0),
                          int(num(r["Loss of Eye"]) or 0), r["Inspection"].strip() or None,
                          r["NatureTitle"].strip() or None, r["Part of Body Title"].strip() or None,
                          r["Event"].strip() or None, title or None, r["Source"].strip() or None,
                          r["SourceTitle"].strip() or None, r["Final Narrative"] or None,
                          sorted({c for c, _ in categorize(r)}), weather, f"SRID=4326;POINT({lon} {lat})"))
            n += 1
    print(f"  osha_injuries: {n}")


def load_cdc(conn):
    path = DATA / "cdc_wonder_weather_deaths.txt"
    if not path.exists():
        print("  weather_fatalities: skipped (run scraper/cdc_wonder.py first)")
        return
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"):
        fips = (r.get("County Code") or "").strip()
        if fips.isdigit():
            d = num(r["Deaths"])
            rows.append((fips.zfill(5), r["County"], datetime.date(2018, 1, 1), datetime.date(2024, 12, 31), "X30-X39",
                         int(d) if d is not None else None, d is None, int(num(r["Population"]) or 0) or None,
                         num(r["Crude Rate"])))
    conn.cursor().executemany("INSERT INTO weather_fatalities VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
    print(f"  weather_fatalities: {len(rows)} counties")


def load_weather(conn):
    stations = {r[0] for r in conn.execute("SELECT station_id FROM weather_stations")}
    n = 0
    with conn.cursor().copy("COPY weather_daily (station_id, day, max_temp_f, min_temp_f, max_heat_index_f, "
                            "max_dewpoint_f, min_rh, avg_rh, precip_in, max_gust_kt, max_wind_kt) FROM STDIN") as cp:
        for r in csv.DictReader(open(DATA / "station_weather_daily.csv", encoding="utf-8")):
            if r["station"] in stations:
                cp.write_row((r["station"], r["date"], *(num(r[k]) for k in (
                    "max_temp_f", "min_temp_f", "max_feel", "max_dewpoint_f", "min_rh", "avg_rh", "precip_in",
                    "max_wind_gust_kts", "max_wind_speed_kts"))))
                n += 1
    print(f"  weather_daily: {n}")


def main():
    with psycopg.connect(database_url()) as conn:  # one transaction: a failed load leaves the old data in place
        box = conn.execute("SELECT min(ST_Y(geog::geometry)) - %s, max(ST_Y(geog::geometry)) + %s, "
                           "min(ST_X(geog::geometry)) - %s, max(ST_X(geog::geometry)) + %s FROM sites",
                           (PAD_DEG,) * 4).fetchone()
        if box[0] is None:
            raise SystemExit("No sites loaded; run db/setup_db.py first.")
        category_of = {t: c for c, types in conn.execute("SELECT category, noaa_event_types FROM hazard_categories")
                       for t in types}
        conn.execute("TRUNCATE storm_events, storm_event_locations, storm_fatalities, osha_injuries, "
                     "weather_fatalities, weather_daily, site_hazard_events")
        print("Loading history...")
        load_storms(conn, box, category_of)
        load_osha(conn, box)
        load_cdc(conn)
        load_weather(conn)
        print("Tying sites to hazard events...")
        print(f"  site_hazard_events: {conn.execute('SELECT refresh_site_hazard_events()').fetchone()[0]}")
    with psycopg.connect(database_url(), autocommit=True) as conn:
        conn.execute("REFRESH MATERIALIZED VIEW site_risk_profile")
        print("site_risk_profile refreshed.")
        print("Database size:", conn.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0])


if __name__ == "__main__":
    main()
