"""Past NWS watches, warnings and advisories for the sites' states (IEM VTEC archive), by county/zone.

Writes data/nws_warning_history.csv: group, phenomena, significance, ugc, issued (UTC), expires (UTC).
The hazard model turns these into "a <group> alert issued by 6 PM the day before covered this site's zone",
which is the same thing it can see live at briefing time. Run from the project root:

    python scraper/nws_warning_history.py
"""
import csv
import datetime as dt
import io
import time

from scrape_power_plants import DATA, get

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py"
STATES = ("FL", "GA", "AL")
START_YEAR = 2021
# VTEC phenomena -> hazard group. Significance: W warning, A watch, Y advisory.
GROUPS = {
    "heat": [("HT", "Y"), ("EH", "W"), ("EH", "A"), ("XH", "W"), ("XH", "A")],
    "flood": [("FA", "A"), ("FF", "A"), ("FL", "W"), ("FL", "A")],
    "wind": [("WI", "Y"), ("HW", "W"), ("HW", "A")],
    "tropical": [("TR", "W"), ("TR", "A"), ("HU", "W"), ("HU", "A"), ("SS", "W"), ("SS", "A")],
    "cold": [("FZ", "W"), ("FZ", "A"), ("FR", "Y"), ("HZ", "W"), ("HZ", "A"), ("WC", "Y"), ("WC", "W"), ("CW", "Y"),
             ("EC", "W"), ("EC", "A"), ("WS", "W"), ("WS", "A"), ("WW", "Y")],
    "fire": [("FW", "W"), ("FW", "A")],
    "fog": [("FG", "Y")],
    "convective": [("TO", "A"), ("SV", "A")],
}
GROUP_OF = {code: g for g, codes in GROUPS.items() for code in codes}


def main():
    pairs = [code for codes in GROUPS.values() for code in codes]
    query = "&".join(f"states={s}" for s in STATES) + "&" + "&".join(f"phenomena={p}" for p, _ in pairs) + "&" + \
            "&".join(f"significance={s}" for _, s in pairs) + "&limitps=1&location_group=states&accept=csv"
    rows = []
    for year in range(START_YEAR, dt.date.today().year + 1):
        url = f"{IEM}?{query}&sts={year}-01-01T00:00Z&ets={year + 1}-01-01T00:00Z"
        for r in csv.DictReader(io.StringIO(get(url).decode("utf-8", "replace"))):
            g = GROUP_OF.get((r["phenomena"], r["significance"]))
            if g and r["ugc"] and r["gtype"] in ("C", "Z"):
                rows.append((g, r["phenomena"], r["significance"], r["ugc"], r["utc_issue"], r["utc_expire"]))
        print(f"  {year}: {len(rows)} zone/county alerts so far", flush=True)
        time.sleep(5)
    with open(DATA / "nws_warning_history.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "phenomena", "significance", "ugc", "issued_utc", "expires_utc"])
        w.writerows(sorted(set(rows)))
    print(f"Wrote {len(set(rows))} rows to {DATA / 'nws_warning_history.csv'}")


if __name__ == "__main__":
    main()
