"""Hypothesis 2: after a weather impact, business bounces back above normal because of restoration work.

    python scraper/contract_data.py          # first (USAspending, SEC, OpenFEMA incl. fema_national)
    python -m ml.bounceback                  # tests -> ml/models/bounceback.json and the weather_bounceback table

Three tests, each against a "no storm" comparison so seasons and booms everywhere don't pass for a bounceback:
  A. Field-contract business in the hit county (USAspending, FL/GA/AL, county x month): new contracts and dollars
     obligated, months -3..+12 around a storm's first month. Each value is compared with the same county's normal
     for that calendar month and with the same month in every other county (a difference-in-differences), 95%
     intervals by bootstrapping the storm events. The naive version (county mean only) is kept for comparison: it
     shows a false hail "bounce" that is really the September fiscal-year-end spike in contract awards.
  B. National crew contractors' revenue (SEC, quarterly growth vs the same quarter a year earlier) against major
     weather disasters declared nationwide that quarter and the two before (FEMA), with company fixed effects.
  C. The sites' utilities (EIA-861M monthly revenue growth vs a year earlier), months around a tropical storm in
     their own site counties.

"Supported" = the bounce (average of months +1..+6, minus the 3 months before) has a 95% interval above zero.
Only supported effects are written to weather_bounceback with supported = true; the API shows those to supervisors.
"""
import csv
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from scipy import stats

from db.setup_db import database_url
from ml.contract_disruption import FEMA_WEATHER, UTILITY_SITES, storm_days, zone_counties

ROOT = Path(__file__).resolve().parent.parent
DATA, MODELS = ROOT / "data", ROOT / "ml" / "models"
MODEL_VERSION = "bounceback-v1"
KINDS = ["tropical", "tornado", "flood", "hail", "winter", "fema_declaration"]
PRE, POST = 3, 12           # months shown around a storm
BOUNCE = range(1, 7)        # months +1..+6 = the bounceback window
CLEAN = 3                   # a storm month counts as an onset only if the kind was absent the 3 months before
rng = np.random.default_rng(0)


def county_months(start, end):
    """Every FL/GA/AL county x month (balanced panel), from the NWS zone-county file."""
    fips = sorted({f for counties in zone_counties().values() for f in counties if f[:2] in ("12", "13", "01")})
    months = pd.period_range(start, end, freq="M")
    return pd.MultiIndex.from_product([fips, months], names=["county_fips", "month"])


def contract_panel(index):
    """County x month: new field contracts and dollars obligated (new awards and changes), from the USAspending file."""
    z = zipfile.ZipFile(DATA / "raw" / "usaspending" / "field_contract_transactions.zip")
    rows = []
    for name in (n for n in z.namelist() if "PrimeTransactions" in n):
        with z.open(name) as f:
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                fips = r["prime_award_transaction_place_of_performance_county_fips_code"]
                if fips and r["action_date"]:
                    rows.append((fips.zfill(5), r["action_date"][:7], r["modification_number"] in ("0", ""),
                                 float(r["federal_action_obligation"] or 0)))
    t = pd.DataFrame(rows, columns=["county_fips", "month", "new", "usd"])
    t["month"] = pd.PeriodIndex(t.month, freq="M")
    g = t.groupby(["county_fips", "month"]).agg(new_contracts=("new", "sum"), obligated_usd=("usd", "sum"))
    p = g.reindex(index, fill_value=0)
    p["log_obligated"] = np.log1p(p.obligated_usd.clip(lower=0))
    return p


def storm_months(conn, index):
    """County x month x kind: 1 if that weather hit the county that month (NOAA days; FEMA weather declarations)."""
    days = storm_days(conn)
    days["month"] = pd.to_datetime(days.day).dt.to_period("M")
    s = days.groupby(["county_fips", "month", "kind"]).size().unstack(fill_value=0).reindex(index, fill_value=0)
    fema = pd.read_csv(DATA / "fema_declarations.csv", dtype={"county_fips": str})
    fema = fema[fema.incident_type.isin(FEMA_WEATHER)]
    fema["month"] = pd.to_datetime(fema.incident_begin).dt.to_period("M")
    s["fema_declaration"] = fema.groupby(["county_fips", "month"]).size().reindex(index, fill_value=0)
    return (s.reindex(columns=KINDS, fill_value=0) > 0).astype(int)


def two_way_residual(y):
    """y minus its county mean and its calendar-month mean (balanced panel: exact two-way fixed effects)."""
    df = y.unstack("month")
    return (df.sub(df.mean(axis=1), axis=0) - df.mean(axis=0) + df.values.mean()).stack()


def seasonal_residual(y, rounds=20):
    """y minus each county's own calendar-month pattern and minus each year-month's mean (iterated to converge).
    The main test: federal awards spike at fiscal year end (September), more in counties with many contracts, so a
    spring storm followed by a September spike looks like a "bounce" unless each county's seasons are removed."""
    df = y.unstack("month")
    cal = np.array([m.month for m in df.columns])
    r = df.copy()
    for _ in range(rounds):
        for month in range(1, 13):
            cols = df.columns[cal == month]
            r[cols] = r[cols].sub(r[cols].mean(axis=1), axis=0)
        r = r - r.mean(axis=0)
    return r.stack()


def event_study(resid, hits):
    """Mean residual by month relative to each storm onset, with a bootstrap 95% interval for the bounce."""
    r = resid.unstack("month")
    h = hits.unstack("month").reindex(index=r.index, columns=r.columns, fill_value=0).to_numpy()
    R = r.to_numpy()
    n_months = R.shape[1]
    ks = list(range(-PRE, POST + 1))
    events = []
    for i in range(h.shape[0]):
        for m in np.flatnonzero(h[i]):
            if m - CLEAN < 0 or h[i, m - CLEAN:m].any() or m + max(BOUNCE) >= n_months:
                continue  # need a clean onset and the whole bounce window inside the data
            events.append([R[i, m + k] if 0 <= m + k < n_months else np.nan for k in ks])
    E = np.array(events)
    if len(E) < 10:
        return None
    pre_cols = [ks.index(k) for k in range(-PRE, 0)]
    post_cols = [ks.index(k) for k in BOUNCE]
    bounce = lambda M: np.nanmean(M[:, post_cols]) - np.nanmean(M[:, pre_cols])
    boot = np.array([bounce(E[rng.integers(0, len(E), len(E))]) for _ in range(2000)])
    return {"events": len(E), "by_month": {k: round(float(np.nanmean(E[:, j])), 4) for j, k in enumerate(ks)},
            "month0": round(float(np.nanmean(E[:, ks.index(0)])), 4), "bounce": round(float(bounce(E)), 4),
            "ci_low": round(float(np.percentile(boot, 2.5)), 4), "ci_high": round(float(np.percentile(boot, 97.5)), 4)}


# ---------------------------------------------------------------------------------- B. national contractors
def ols(y, X):
    """OLS with heteroskedasticity-robust (HC1) standard errors."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    n, k = X.shape
    bread = np.linalg.pinv(X.T @ X)
    cov = bread @ (X.T * e ** 2) @ X @ bread * n / (n - k)
    return beta, cov


def national_test():
    rev = pd.read_csv(DATA / "company_quarterly_revenue.csv")
    rev["q"] = pd.PeriodIndex(pd.to_datetime(rev.quarter_end), freq="Q")
    rev = rev.sort_values(["cik", "q"]).drop_duplicates(["cik", "q"], keep="last")
    prev = rev.assign(q=rev.q + 4)[["cik", "q", "revenue_usd"]].rename(columns={"revenue_usd": "prev"})
    rev = rev.merge(prev, on=["cik", "q"])
    rev["yoy"] = np.log(rev.revenue_usd / rev.prev)
    d = pd.read_csv(DATA / "fema_national_declarations.csv")
    d = d[d.incident_type.isin(FEMA_WEATHER)]
    d["q"] = pd.PeriodIndex(pd.to_datetime(d.incident_begin), freq="Q")
    load = np.log1p(d.groupby("q").size())          # counties declared nationwide that quarter (log)
    load = (load - load.mean()) / load.std()
    out = {}
    for kind in ("contractor", "utility"):
        g = rev[rev.kind == kind].copy()
        for lag in (0, 1, 2):
            g[f"z{lag}"] = (g.q - lag).map(load)
        g = g.dropna(subset=["z0", "z1", "z2", "yoy"])
        firms = pd.get_dummies(g.company, dtype=float)
        X = np.column_stack([g[["z0", "z1", "z2"]].to_numpy(), firms.to_numpy()])
        beta, cov = ols(g.yoy.to_numpy(), X)
        b = beta[1] + beta[2]                            # following two quarters = the bounce
        se = np.sqrt(cov[1, 1] + cov[2, 2] + 2 * cov[1, 2])
        out[kind] = {"companies": firms.shape[1], "quarters": len(g),
                     "same_quarter": round(float(beta[0]), 4), "same_quarter_p": round(float(2 * stats.norm.sf(abs(beta[0] / np.sqrt(cov[0, 0])))), 4),
                     "bounce_next_2_quarters": round(float(b), 4), "ci_low": round(float(b - 1.96 * se), 4),
                     "ci_high": round(float(b + 1.96 * se), 4), "p_value": round(float(2 * stats.norm.sf(abs(b / se))), 4),
                     "unit": "change in log revenue growth per 1 SD more counties declared nationwide"}
    return out


# --------------------------------------------------------------------------------------------- C. utilities
def utility_test(conn, hits):
    sales = pd.DataFrame(conn.execute("SELECT utility_name, month, revenue_thousand_usd FROM utility_monthly_sales").fetchall())
    sales["month"] = pd.PeriodIndex(pd.to_datetime(sales.month), freq="M")
    sales = sales.groupby(["utility_name", "month"]).revenue_thousand_usd.sum().reset_index()
    prev = sales.assign(month=sales.month + 12).rename(columns={"revenue_thousand_usd": "prev"})
    sales = sales.merge(prev, on=["utility_name", "month"])
    sales = sales[(sales.revenue_thousand_usd > 0) & (sales.prev > 0)]
    sales["yoy"] = np.log(sales.revenue_thousand_usd.astype(float) / sales.prev.astype(float))
    site_counties = pd.DataFrame(conn.execute("SELECT county_fips, utility_name FROM sites WHERE county_fips IS NOT NULL").fetchall())
    tropical = hits["tropical"].unstack("month")
    rows = []
    for name, g in sales.groupby("utility_name"):
        counties = site_counties[site_counties.utility_name == name].county_fips.unique()
        hit = tropical.reindex(counties).fillna(0).max(axis=0)       # any of its site counties hit that month
        s = g.set_index("month").yoy
        s = s - s.mean()
        for m in hit[hit > 0].index:
            if hit.reindex([m - k for k in range(1, CLEAN + 1)]).fillna(0).any():
                continue
            rows.append({k: s.get(m + k, np.nan) for k in range(-PRE, 7)})
    if len(rows) < 5:
        return {"events": len(rows), "note": "too few tropical storm onsets in the utilities' site counties"}
    E = pd.DataFrame(rows)
    bounce = lambda M: M[list(BOUNCE)].stack().mean() - M[list(range(-PRE, 0))].stack().mean()
    boot = [bounce(E.iloc[rng.integers(0, len(E), len(E))]) for _ in range(2000)]
    return {"events": len(E), "by_month": {k: round(float(E[k].mean()), 4) for k in E.columns},
            "bounce": round(float(bounce(E)), 4), "ci_low": round(float(np.nanpercentile(boot, 2.5)), 4),
            "ci_high": round(float(np.nanpercentile(boot, 97.5)), 4), "unit": "log revenue growth vs a year earlier"}


def main():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        index = county_months("2015-10", "2026-06")
        panel = contract_panel(index)
        hits = storm_months(conn, index)
        regional, naive = {}, {}
        mean_new = panel.new_contracts.mean()
        for outcome in ("new_contracts", "log_obligated"):
            y = panel[outcome].astype(float)
            for name, resid, store in (("seasonal", seasonal_residual(y), regional), ("naive", two_way_residual(y), naive)):
                for kind in KINDS:
                    r = event_study(resid, hits[kind])
                    if r:
                        r["supported"] = r["ci_low"] > 0
                        r["relative"] = round(r["bounce"] / mean_new, 3) if outcome == "new_contracts" else round(float(np.expm1(r["bounce"])), 3)
                        store[f"{outcome}:{kind}"] = r
        national = national_test()
        utilities = utility_test(conn, hits)

        rows = []
        for key, r in regional.items():
            outcome, kind = key.split(":")
            rows.append(("county_field_contracts", kind, outcome, "months +1..+6", r["bounce"], r["ci_low"], r["ci_high"],
                         r["relative"], r["events"], bool(r["supported"])))
        for kind, r in national.items():
            rows.append((f"national_{kind}_revenue", "major_disaster", "yoy_revenue_growth", "next 2 quarters",
                         r["bounce_next_2_quarters"], r["ci_low"], r["ci_high"], None, r["quarters"], r["ci_low"] > 0))
        if "bounce" in utilities:
            rows.append(("site_utility_revenue", "tropical", "yoy_revenue_growth", "months +1..+6", utilities["bounce"],
                         utilities["ci_low"], utilities["ci_high"], None, utilities["events"], utilities["ci_low"] > 0))
        conn.execute("DELETE FROM weather_bounceback")
        conn.cursor().executemany(
            "INSERT INTO weather_bounceback (scope, weather_kind, outcome, horizon, effect, ci_low, ci_high, relative, "
            "events, supported, model_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(*r, MODEL_VERSION) for r in rows])
        conn.commit()
    result = {"model_version": MODEL_VERSION, "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "county_months": len(index), "mean_new_contracts_per_county_month": round(float(mean_new), 4),
              "regional": regional, "regional_naive_two_way": naive, "national": national, "utilities": utilities,
              "supported": [r[0] + ":" + r[1] + ":" + r[2] for r in rows if r[-1]]}
    (MODELS / "bounceback.json").write_text(json.dumps(result, indent=2, default=str))
    for key, r in regional.items():
        print(f"{key:32s} events {r['events']:4d}  month0 {r['month0']:+.3f}  bounce {r['bounce']:+.3f} "
              f"[{r['ci_low']:+.3f}, {r['ci_high']:+.3f}]  relative {r['relative']:+.1%}  {'SUPPORTED' if r['supported'] else ''}")
    print("national:", json.dumps(national, indent=1))
    print("utilities:", {k: v for k, v in utilities.items() if k != "by_month"})
    print("supported:", result["supported"])


if __name__ == "__main__":
    main()
