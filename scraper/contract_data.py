"""Public stand-ins for crew contracts, contract failures, company revenue and storm damage (no keys needed).

  1. USAspending.gov: federal field-construction contracts performed in FL/GA/AL (power and utility lines,
     heavy civil, electrical, site work) and every change to them: terminations, change orders, schedule
     extensions, with the county the work was in. -> data/field_contracts.csv (one row per contract)
  2. SEC EDGAR XBRL: quarterly revenue of the public line-crew contractors and the sites' utilities.
     -> data/company_quarterly_revenue.csv
  3. OpenFEMA: disaster declarations by county, Public Assistance utility-repair projects (damage category F), and
     major-disaster declarations nationwide (fema_national, for the national contractors).
     -> data/fema_declarations.csv, data/fema_utility_projects.csv

Run from the project root:  python scraper/contract_data.py [usaspending] [sec] [fema] [fema_national]  (default: all)
SEC asks API clients for a contact: set SEC_USER_AGENT="Your Name your@email" (a project-only default is used).
"""
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.parse
import zipfile
from collections import defaultdict

from scrape_power_plants import DATA, RAW, get

API = "https://api.usaspending.gov/api/v2"
STATES = ["FL", "GA", "AL"]
STATE_FIPS = {"12": "FL", "13": "GA", "01": "AL"}
# Outdoor crew work: utility line and system construction, highway/heavy civil, electrical, site preparation
NAICS = ["2371", "2373", "2379", "238210", "2389"]
FY_START = "2015-10-01"  # weather history starts Jan 2015
JSON_HEADERS = {"User-Agent": "PlanetControl/0.1 (SwampHacks 2026 project)", "Content-Type": "application/json"}

TERMINATED = {"E": "default", "X": "cause", "F": "convenience", "N": "cancelled"}  # FPDS reason-for-modification
CHANGE_ORDER = {"D", "L"}
OPTION = "G"   # exercising an option year moves the end date as planned: not a slip
# Weather named in a contract change. Stormwater/storm drain work is scope, not weather, so it's excluded.
WEATHER = re.compile(r"\b(hurricane|tropical storm|tornado|storm(?!\s*(water|drain|sewer|pipe|inlet|system|shelter))"
                     r"|flood(?:ing|ed)?(?!\s*(control|wall|gate|plain|proofing))|lightning(?!\s*(protection|arrest))|weather|wind damage|"
                     r"high winds?|named storm|wet season|rain days?)", re.I)
# ...and only when the change says the weather held the work up. "Hurricane protection project", "storm damage repair"
# or "temporary emergency power - Hurricane Milton" is work the weather created; "weather time extension",
# "Hurricane Ian impacts" or "extend ... due to weather" is a disruption.
DISRUPTION = re.compile(r"\b(delay(?:s|ed)?|extend(?:s|ed)?|extension|time ext|additional time|impacts?|impacted|"
                        r"suspen(?:d|ded|sion)|stop[- ]?work|stoppage|lost days|because of|due to)\b", re.I)
SCOPE = re.compile(r"\b(repair|damage|protection|risk management|emergency power|renourish|recovery|debris)\b", re.I)


def weather_disruption(text):
    """Weather named in a contract change as the reason work was held up (not as the work itself)."""
    if not (text and WEATHER.search(text) and DISRUPTION.search(text)):
        return False
    # "repair damage caused by Hurricane Michael" is new scope; keep it only if it also talks about time
    return not SCOPE.search(text) or bool(re.search(r"\b(delay|extend|extension|time ext|additional time|suspen)", text, re.I))


def post_json(url, body):
    return json.loads(get(url, json.dumps(body).encode(), JSON_HEADERS))


def summarize(rows):
    """One contract's changes (sorted by action date) -> its outcome. Pure function, checked in check()."""
    base = next((r for r in rows if r["modification_number"] in ("0", "")), None)
    if base is None:
        return None  # contract started before our window: its original end date is unknown
    end = lambda r: (r["period_of_performance_current_end_date"] or "")[:10]
    orig = prev = end(base)
    slip, codes, cited = 0, set(), []
    for r in rows:
        code, cur = r["action_type_code"], end(r)
        if r is not base:
            codes.add(code)
            if weather_disruption(r["transaction_description"]):
                cited.append(r["transaction_description"][:160])
        if cur and prev and cur > prev and code != OPTION and r is not base:
            slip += (dt.date.fromisoformat(cur) - dt.date.fromisoformat(prev)).days
        prev = cur or prev
    last = rows[-1]
    return {
        "award_key": base["contract_award_unique_key"], "piid": base["award_id_piid"],
        "recipient": base["recipient_name"], "recipient_parent": base["recipient_parent_name"],
        "recipient_uei": base["recipient_uei"], "agency": base["awarding_agency_name"],
        "sub_agency": base["awarding_sub_agency_name"], "naics": base["naics_code"],
        "naics_description": base["naics_description"],
        "description": (base["prime_award_base_transaction_description"] or base["transaction_description"] or "")[:300],
        "county_fips": base["prime_award_transaction_place_of_performance_county_fips_code"],
        "county_name": base["primary_place_of_performance_county_name"],
        "state": base["primary_place_of_performance_state_code"],
        "city": base["primary_place_of_performance_city_name"],
        "start_date": (base["period_of_performance_start_date"] or base["action_date"])[:10],
        "original_end_date": orig, "final_end_date": prev,
        "last_action_date": last["action_date"][:10],
        "obligated_usd": last["total_dollars_obligated"] or 0,
        "potential_value_usd": last["potential_total_value_of_award"] or 0,
        "mods": len(rows) - 1, "change_orders": sum(r["action_type_code"] in CHANGE_ORDER for r in rows if r is not base),
        "terminated": next((TERMINATED[c] for c in "EXFN" if c in codes), ""),
        "slip_days": slip, "weather_cited": bool(cited), "weather_cited_text": " | ".join(cited[:3]),
    }


def check():
    row = lambda mod, code, end, desc="": {
        "modification_number": mod, "action_type_code": code, "period_of_performance_current_end_date": end,
        "transaction_description": desc, "contract_award_unique_key": "K", "award_id_piid": "P", "recipient_name": "R",
        "recipient_parent_name": "R", "recipient_uei": "U", "awarding_agency_name": "A", "awarding_sub_agency_name": "S",
        "naics_code": "237130", "naics_description": "N", "prime_award_base_transaction_description": "BASE",
        "prime_award_transaction_place_of_performance_county_fips_code": "12086",
        "primary_place_of_performance_county_name": "MIAMI-DADE", "primary_place_of_performance_state_code": "FL",
        "primary_place_of_performance_city_name": "MIAMI", "period_of_performance_start_date": "2022-01-01",
        "action_date": "2022-01-01", "total_dollars_obligated": "100", "potential_total_value_of_award": "100"}
    s = summarize([row("0", "", "2022-06-30"), row("P01", "G", "2023-06-30"),          # option year: planned
                   row("P02", "B", "2023-08-29", "Extension due to Hurricane Ian damage"),  # +60 days: slip
                   row("P03", "E", "2023-08-29")])
    assert (s["slip_days"], s["terminated"], s["weather_cited"], s["mods"]) == (60, "default", True, 3), s
    assert not WEATHER.search("Install stormwater drain and flood control gate"), "scope words aren't weather"
    for scope in ("Beach erosion control and hurricane protection project, renourishment",
                  "Additional work necessary to repair damage caused by Hurricane Michael",
                  "ACI temporary emergency power - Quadrant II - Hurricane Milton", "Upgrade on the Shawnee unit sally port",
                  "Inspect lightning protection system/POP extension"):
        assert not weather_disruption(scope), scope
    for hit in ("WD003 WEATHER TIME EXTENSION OCT 24 - NOV 24", "HURRICANE IAN IMPACTS", "REA 0002 - FLOODING DELAYS 2021",
                "Extend the period of performance to March 1, 2019 due to weather (wet season)"):
        assert weather_disruption(hit), hit
    assert summarize([row("P01", "B", "2023-01-01")]) is None  # no base transaction in the window


# ------------------------------------------------------------------------------------------------- USAspending
def usaspending():
    out = RAW / "usaspending"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "field_contract_transactions.zip"
    if not path.exists():
        filters = {"award_type_codes": ["A", "B", "C", "D"], "naics_codes": {"require": NAICS},
                   "place_of_performance_locations": [{"country": "USA", "state": s} for s in STATES],
                   "time_period": [{"start_date": FY_START, "end_date": dt.date.today().isoformat()}]}
        job = post_json(f"{API}/download/transactions/", {"filters": filters, "file_format": "csv", "limit": 500000})
        print("  USAspending is building the file…", flush=True)
        for _ in range(120):
            status = json.loads(get(job["status_url"], headers=JSON_HEADERS))
            if status["status"] in ("finished", "failed"):
                break
            time.sleep(10)
        if status["status"] != "finished":
            raise SystemExit(f"USAspending download failed: {status.get('message')}")
        path.write_bytes(get(status["file_url"], headers=JSON_HEADERS))
    z = zipfile.ZipFile(path)
    by_award = defaultdict(list)
    for name in (n for n in z.namelist() if "PrimeTransactions" in n):
        with z.open(name) as f:
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                if r["primary_place_of_performance_state_code"] in STATES:
                    by_award[r["contract_award_unique_key"]].append(r)
    contracts = []
    for rows in by_award.values():
        rows.sort(key=lambda r: (r["action_date"], r["modification_number"]))
        if (s := summarize(rows)) and s["county_fips"]:
            contracts.append(s)
    with open(DATA / "field_contracts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(contracts[0]))
        w.writeheader()
        w.writerows(contracts)
    print(f"  {len(contracts)} contracts with their full history ({len(by_award)} seen): "
          f"{sum(1 for c in contracts if c['terminated'])} terminated, "
          f"{sum(1 for c in contracts if c['slip_days'] >= 30)} slipped 30+ days, "
          f"{sum(c['weather_cited'] for c in contracts)} name weather in a change")


# --------------------------------------------------------------------------------------------------------- SEC
COMPANIES = [  # (CIK, name, kind)
    (1050915, "Quanta Services", "contractor"), (15615, "MasTec", "contractor"), (700923, "MYR Group", "contractor"),
    (67215, "Dycom Industries", "contractor"), (1361538, "Primoris Services", "contractor"),
    (105634, "EMCOR Group", "contractor"), (861459, "Granite Construction", "contractor"),
    (874238, "Sterling Infrastructure", "contractor"),
    # Utility parents: FPL, Georgia Power, Alabama Power and Duke Energy Florida file combined reports whose XBRL
    # revenue sits under the parent, so the parent's numbers stand in for them.
    (753308, "NextEra Energy", "utility"), (92122, "Southern Company", "utility"),
    (1326160, "Duke Energy", "utility"), (96271, "Tampa Electric", "utility"),
]
REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "ElectricUtilityRevenue",
                "RegulatedAndUnregulatedOperatingRevenue", "SalesRevenueNet"]


def quarters(facts):
    """{quarter_end: (revenue, tag)}: 3-month values from 10-Qs, Q4 = fiscal year minus its first three quarters."""
    got = {}
    for tag in REVENUE_TAGS:  # first tag wins for each quarter
        vals = facts.get(tag, {}).get("units", {}).get("USD", [])
        q, yr = {}, {}
        for v in vals:
            if "start" not in v:
                continue
            days = (dt.date.fromisoformat(v["end"]) - dt.date.fromisoformat(v["start"])).days
            (q if 80 <= days <= 100 else yr if 350 <= days <= 380 else {})[(v["start"], v["end"])] = v["val"]
        for (start, end), val in q.items():
            got.setdefault(end, (val, tag))
        for (start, end), total in yr.items():
            inside = [val for (s, e), val in q.items() if s >= start and e < end]
            if len(inside) == 3:
                got.setdefault(end, (total - sum(inside), tag))
    return got


def sec():
    ua = {"User-Agent": os.environ.get("SEC_USER_AGENT", "PlanetControl SwampHacks2026 research project")}
    rows = []
    for cik, name, kind in COMPANIES:
        try:
            facts = json.loads(get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=ua))
        except Exception as e:  # a subsidiary that stopped filing, etc.
            print(f"  {name}: {e}")
            continue
        q = quarters(facts["facts"].get("us-gaap", {}))
        rows += [{"cik": cik, "company": name, "kind": kind, "quarter_end": end, "revenue_usd": val, "tag": tag}
                 for end, (val, tag) in sorted(q.items()) if end >= "2014-01-01"]
        print(f"  {name}: {sum(1 for e in q if e >= '2014-01-01')} quarters", flush=True)
        time.sleep(0.2)  # SEC allows 10 requests/s
    with open(DATA / "company_quarterly_revenue.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["cik", "company", "kind", "quarter_end", "revenue_usd", "tag"])
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------------------------------------------- FEMA
FEMA = "https://www.fema.gov/api/open/v2"


def fema_pages(dataset, flt, select):
    out, skip = [], 0
    while True:
        q = urllib.parse.urlencode({"$filter": flt, "$select": select, "$top": 1000, "$skip": skip})
        page = json.loads(get(f"{FEMA}/{dataset}?{q}", headers=JSON_HEADERS))[dataset]
        out += page
        if len(page) < 1000:
            return out
        skip += 1000


def fema():
    states = " or ".join(f"state eq '{s}'" for s in STATES)
    decl = fema_pages("DisasterDeclarationsSummaries", f"({states}) and incidentBeginDate ge '2015-01-01T00:00:00.000Z'",
                      "disasterNumber,fipsStateCode,fipsCountyCode,incidentType,declarationTitle,incidentBeginDate,"
                      "incidentEndDate,declarationDate")
    with open(DATA / "fema_declarations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["disaster_number", "county_fips", "incident_type", "title", "incident_begin", "incident_end", "declared"])
        w.writerows({(d["disasterNumber"], d["fipsStateCode"] + d["fipsCountyCode"]): [
            d["disasterNumber"], d["fipsStateCode"] + d["fipsCountyCode"], d["incidentType"], d["declarationTitle"],
            d["incidentBeginDate"][:10], (d["incidentEndDate"] or d["incidentBeginDate"])[:10], d["declarationDate"][:10]]
            for d in decl if d["fipsCountyCode"] != "000"}.values())
    st = " or ".join(f"stateAbbreviation eq '{s}'" for s in STATES)
    proj = fema_pages("PublicAssistanceFundedProjectsDetails", f"damageCategoryCode eq 'F' and ({st})",
                      "disasterNumber,pwNumber,stateNumberCode,countyCode,incidentType,applicationTitle,declarationDate,"
                      "projectAmount")
    with open(DATA / "fema_utility_projects.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["disaster_number", "pw_number", "county_fips", "incident_type", "title", "declared", "project_usd"])
        w.writerows([p["disasterNumber"], p["pwNumber"],
                     f"{int(p['stateNumberCode']):02d}{int(p['countyCode']):03d}" if p.get("countyCode") is not None else "",
                     p["incidentType"], (p["applicationTitle"] or "")[:200], p["declarationDate"][:10], p["projectAmount"]]
                    for p in proj)
    print(f"  FEMA: {len(decl)} county declarations, {len(proj)} utility-repair projects")


def fema_national():
    """Major-disaster declarations (DR) in every state since 2013: national contractors' storm workload."""
    rows = fema_pages("DisasterDeclarationsSummaries", "declarationType eq 'DR' and incidentBeginDate ge '2013-01-01T00:00:00.000Z'",
                      "disasterNumber,state,fipsStateCode,fipsCountyCode,incidentType,incidentBeginDate,declarationDate")
    with open(DATA / "fema_national_declarations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["disaster_number", "state", "county_fips", "incident_type", "incident_begin", "declared"])
        w.writerows([d["disasterNumber"], d["state"], d["fipsStateCode"] + d["fipsCountyCode"], d["incidentType"],
                     d["incidentBeginDate"][:10], d["declarationDate"][:10]] for d in rows)
    print(f"  FEMA national: {len(rows)} county designations")


if __name__ == "__main__":
    check()
    jobs = {"usaspending": usaspending, "sec": sec, "fema": fema, "fema_national": fema_national}
    for job in sys.argv[1:] or list(jobs):
        print(job, flush=True)
        jobs[job]()
