"""Loaded labor cost per crew hour from BLS, the public stand-in for company payroll figures.

  wage:      OEWS mean hourly wage, Electrical Power-Line Installers and Repairers (SOC 49-9051), by state
  benefits:  ECEC private industry, total compensation / wages and salaries per hour (latest quarter)
  loaded = wage x benefits multiplier

Writes data/labor_costs.csv; ml/economic_impact.py loads it into economic_assumptions (only over values that are
still assumptions or earlier BLS figures, never over numbers a company has entered). Run from the project root:

    python scraper/labor_costs.py
"""
import csv
import json
import urllib.request

from scrape_power_plants import DATA

API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
STATES = {"FL": "12", "GA": "13", "AL": "01"}
NATIONAL_WAGE = "OEUN000000000000049905103"
ECEC_TOTAL, ECEC_WAGES = "CMU2010000000000D", "CMU2020000000000D"  # private industry, $ per hour worked


def latest(series):
    with urllib.request.urlopen(API + series, timeout=60) as r:
        d = json.load(r)
    if d.get("status") != "REQUEST_SUCCEEDED" or not d["Results"]["series"][0]["data"]:
        raise SystemExit(f"BLS series {series}: {d.get('message')}")
    x = d["Results"]["series"][0]["data"][0]
    return float(x["value"]), f"{x['year']} {x['periodName'] if 'periodName' in x else x['period']}"


def main():
    total, period = latest(ECEC_TOTAL)
    wages, _ = latest(ECEC_WAGES)
    mult = round(total / wages, 3)
    rows = []
    for key, series in [("", NATIONAL_WAGE)] + [(f"_{s}", f"OEUS{f}{'0' * 11}49905103") for s, f in STATES.items()]:
        wage, wperiod = latest(series)
        rows.append({"key": f"loaded_cost_per_hour{key}", "value": round(wage * mult, 2), "unit": "USD per person-hour",
                     "note": f"BLS: OEWS 49-9051 mean wage ${wage:.2f} ({wperiod}, {'US' if not key else key[1:]}) x "
                             f"ECEC benefits multiplier {mult} ({period}, private industry; series {series})"})
    with open(DATA / "labor_costs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["key", "value", "unit", "note"])
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"  {r['key']:28} ${r['value']:.2f}/h  ({r['note']})")


if __name__ == "__main__":
    main()
