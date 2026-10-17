"""Power outages by county per day (EAGLE-I, ORNL), the public stand-in for utility crew workload.

Every outage needs restoration crews, so customer-hours without power is a measured proxy for crew trips until real
work-order logs are loaded. Source: EAGLE-I recorded outages, 15-minute county snapshots, 2014-2025
(https://figshare.com/articles/dataset/24237376). The yearly national files are 1-1.5 GB each, so they are streamed
and reduced on the fly to FL/GA/AL county-days; nothing large is stored.

Writes data/eaglei_county_daily.csv (county_fips, date, customer_hours_out, peak_customers_out, customers) and
caches each year in data/raw/eaglei/. Run from the project root:  python scraper/outages.py
"""
import csv
import datetime as dt
import io
import json
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

from scrape_power_plants import DATA, RAW

ARTICLE = "https://api.figshare.com/v2/articles/24237376"
STATES = {"Florida", "Georgia", "Alabama"}
YEARS = range(2021, 2026)          # our weather history starts Oct 2021; EAGLE-I currently ends with 2025
EASTERN = ZoneInfo("America/New_York")
CACHE = RAW / "eaglei"


def files():
    with urllib.request.urlopen(ARTICLE, timeout=60) as r:
        return {f["name"]: f["download_url"] for f in json.load(r)["files"]}


def reduce_year(url):
    """Stream one national year file; return {(fips, date): [customer_hours, peak, customers]} for our states."""
    agg = defaultdict(lambda: [0.0, 0, 0])
    with urllib.request.urlopen(url, timeout=600) as r:
        lines = io.TextIOWrapper(r, encoding="utf-8", errors="replace")
        header = next(lines)
        cols = next(csv.reader([header]))
        i = {c: n for n, c in enumerate(cols)}
        out_col = "customers_out" if "customers_out" in i else "sum"  # the 2023 file names this column "sum"
        for line in lines:
            if not any(f",{s}," in line for s in STATES):  # cheap filter before parsing
                continue
            row = next(csv.reader([line]))
            if row[i["state"]] not in STATES:
                continue
            t = dt.datetime.fromisoformat(row[i["run_start_time"]]).replace(tzinfo=dt.timezone.utc)
            out = int(float(row[i[out_col]] or 0))
            a = agg[(row[i["fips_code"]].zfill(5), t.astimezone(EASTERN).date().isoformat())]
            a[0] += out * 0.25                       # one 15-minute snapshot = a quarter hour per customer out
            a[1] = max(a[1], out)
            a[2] = max(a[2], int(float(row[i["total_customers"]] or 0)) if "total_customers" in i else 0)
    return agg


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    urls = files()
    rows = []
    for year in YEARS:
        path = CACHE / f"eaglei_{year}_fl_ga_al.csv"
        if not path.exists():
            name = f"eaglei_outages_{year}.csv"
            if name not in urls:
                print(f"  {year}: not published yet")
                continue
            print(f"  {year}: streaming {name}…", flush=True)
            agg = reduce_year(urls[name])
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["county_fips", "date", "customer_hours_out", "peak_customers_out", "customers"])
                w.writerows([k[0], k[1], round(v[0], 1), v[1], v[2]] for k, v in sorted(agg.items()))
        year_rows = list(csv.DictReader(open(path)))
        rows += year_rows
        print(f"  {year}: {len(year_rows)} county-days with outages", flush=True)
    with open(DATA / "eaglei_county_daily.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["county_fips", "date", "customer_hours_out", "peak_customers_out", "customers"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} county-days to {DATA / 'eaglei_county_daily.csv'}")


if __name__ == "__main__":
    main()
