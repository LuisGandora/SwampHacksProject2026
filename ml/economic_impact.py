"""Economic impact of weather on the sites' companies.

    python scraper/economics.py        # first: download EIA sales/demand and link plants to companies
    python -m ml.economic_impact       # load it into Tiger, compute weekly impact and correlations

Weekly, per grid operator (balancing authority):
  - demand_mwh: EIA-930 electricity demand (the closest public weekly measure of a utility's sales volume)
  - stop_work_site_days / warning_site_days: days the operator's sites crossed a hazard threshold
  - crew_hours_lost / est_cost_usd: stop-work days x hours_lost_<category> x crew_size x cost, all from
    economic_assumptions (labeled ASSUMPTION until replaced with company data)
  - crew_trips: from crew_trips, when real trip logs are loaded (data/crew_trips.csv, see README)
Monthly, per utility: EIA-861M retail revenue and MWh against the same weather metrics for its own sites.

Correlations are reported raw and deseasonalized (each series minus its same-calendar-month average), because
summer heat and summer demand rise together anyway; the deseasonalized r asks whether an unusually hot or
stormy week also had unusual demand. Correlation is not causation, and n is small: read p-values with care.
"""
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from scipy import stats

from db.setup_db import database_url

DATA = Path(__file__).resolve().parent.parent / "data"
X_METRICS = ["heat_site_days_per_site", "stop_work_site_days_per_site", "storm_events_per_site", "crew_hours_lost"]
MIN_POINTS = 24


def frame(conn, sql, params=()):
    return pd.DataFrame(conn.execute(sql, params).fetchall())


def load_inputs(conn):
    links = pd.read_csv(DATA / "plant_utilities.csv")
    conn.cursor().executemany("UPDATE sites SET utility_id = %s, utility_name = %s, balancing_authority = %s "
                              "WHERE site_id = %s",
                              [(int(r.utility_id), r.utility_name, r.balancing_authority or None, int(r.plant_id))
                               for r in links.fillna({"balancing_authority": ""}).itertuples()])
    sales = pd.read_csv(DATA / "utility_monthly_sales.csv")
    conn.execute("TRUNCATE utility_monthly_sales")
    with conn.cursor().copy("COPY utility_monthly_sales FROM STDIN") as cp:
        for r in sales.groupby(["utility_id", "utility_name", "state", "year", "month"], as_index=False).sum().itertuples():
            cp.write_row((r.utility_id, r.utility_name, r.state, dt.date(r.year, r.month, 1),
                          r.revenue_thousand_usd, r.sales_mwh, int(r.customers)))
    demand = pd.read_csv(DATA / "ba_daily_demand.csv")
    conn.execute("TRUNCATE ba_daily_demand")
    with conn.cursor().copy("COPY ba_daily_demand FROM STDIN") as cp:
        for r in demand.groupby(["balancing_authority", "date"], as_index=False).sum().itertuples():
            cp.write_row((r.balancing_authority, r.date, r.demand_mwh))
    trips = DATA / "crew_trips.csv"
    if trips.exists():
        t = pd.read_csv(trips)
        conn.cursor().executemany(
            "INSERT INTO crew_trips (trip_id, site_id, started_at, ended_at, crew_size, purpose, cost_usd) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (trip_id) DO NOTHING",
            [tuple(None if pd.isna(v) else v for v in r) for r in
             t[["trip_id", "site_id", "started_at", "ended_at", "crew_size", "purpose", "cost_usd"]].itertuples(index=False)])
    return len(links), len(sales), len(demand)


def crew_hours(conn):
    """Stop-work site-days with lost crew hours (capped at one shift per site-day), per site and day."""
    a = {r["key"]: float(r["value"]) for r in conn.execute("SELECT key, value FROM economic_assumptions")}
    stops = frame(conn, "SELECT site_id, day, category FROM site_weather_days WHERE level = 'stop_work'")
    stops["hours"] = stops["category"].map(lambda c: a.get(f"hours_lost_{c}", 0.0))
    per_day = stops.groupby(["site_id", "day"], as_index=False)["hours"].sum()
    per_day["hours"] = per_day["hours"].clip(upper=a.get("shift_hours", 10.0)) * a["crew_size"]
    return per_day, a


def weekly(conn):
    sites = frame(conn, "SELECT site_id, balancing_authority AS ba, utility_name FROM sites")
    days = frame(conn, "SELECT site_id, day, category, level FROM site_weather_days")
    days = days.merge(sites, on="site_id")
    days["week"] = pd.to_datetime(days["day"]).dt.to_period("W-SUN").dt.start_time
    lost, a = crew_hours(conn)
    lost = lost.merge(sites, on="site_id")
    lost["week"] = pd.to_datetime(lost["day"]).dt.to_period("W-SUN").dt.start_time
    storms = frame(conn, """SELECT s.balancing_authority AS ba, date_trunc('week', h.event_time)::date AS week,
                                   count(DISTINCT h.source_id) AS storm_events
                              FROM site_hazard_events h JOIN sites s USING (site_id)
                             WHERE h.source = 'noaa_storm_event' GROUP BY 1, 2""")
    storms["week"] = pd.to_datetime(storms["week"])
    demand = frame(conn, "SELECT balancing_authority AS ba, day, demand_mwh FROM ba_daily_demand")
    demand["week"] = pd.to_datetime(demand["day"]).dt.to_period("W-SUN").dt.start_time
    demand = demand.groupby(["ba", "week"]).agg(demand_mwh=("demand_mwh", "sum"), n=("day", "count")).reset_index()
    demand = demand[demand["n"] == 7].drop(columns="n")  # full weeks only
    trips = frame(conn, """SELECT s.balancing_authority AS ba, date_trunc('week', t.started_at)::date AS week,
                                  count(*) AS crew_trips FROM crew_trips t JOIN sites s USING (site_id) GROUP BY 1, 2""")

    g = days.groupby(["ba", "week"])
    w = pd.DataFrame({
        "stop_work_site_days": g.apply(lambda x: (x["level"] == "stop_work").sum(), include_groups=False),
        "warning_site_days": g.apply(lambda x: (x["level"] == "warning").sum(), include_groups=False),
        "heat_site_days": g.apply(lambda x: (x["category"] == "extreme_heat").sum(), include_groups=False),
    }).reset_index()
    w = w.merge(lost.groupby(["ba", "week"])["hours"].sum().rename("crew_hours_lost").reset_index(), how="left")
    w = w.merge(storms, how="left").merge(demand, how="left")
    if not trips.empty:
        trips["week"] = pd.to_datetime(trips["week"])
        w = w.merge(trips, how="left")
    else:
        w["crew_trips"] = np.nan
    w["sites"] = w["ba"].map(sites.groupby("ba").size())
    w = w.fillna({"crew_hours_lost": 0, "storm_events": 0})
    w["est_cost_usd"] = w["crew_hours_lost"] * a["loaded_cost_per_hour"]
    for c in ("heat_site_days", "stop_work_site_days", "storm_events"):
        w[f"{c}_per_site"] = w[c] / w["sites"]
    return w, sites, lost, days


def correlate(df, xs, y, scope, entity, rows):
    df = df.dropna(subset=[y])
    if len(df) < MIN_POINTS:
        return
    month = pd.to_datetime(df.iloc[:, 0] if "week" not in df else df["week"]).dt.month
    for x in xs:
        if x not in df or df[x].std() == 0:
            continue
        for deseason in (False, True):
            xv, yv = df[x].astype(float), df[y].astype(float)
            if deseason:
                xv, yv = xv - xv.groupby(month).transform("mean"), yv - yv.groupby(month).transform("mean")
            r, p = stats.pearsonr(xv, yv)
            rows.append((scope, entity, x, y, deseason, round(float(r), 3), float(p),
                         round(float(stats.spearmanr(xv, yv)[0]), 3), len(df)))


def run(conn):
    n_links, n_sales, n_demand = load_inputs(conn)
    print(f"loaded {n_links} site-company links, {n_sales} utility-months of sales, {n_demand} grid-operator days")
    w, sites, lost, days = weekly(conn)
    conn.execute("TRUNCATE economic_weekly")
    keep = w.dropna(subset=["ba"])
    conn.cursor().executemany(
        "INSERT INTO economic_weekly VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(r.ba, r.week.date(), int(r.sites), None if pd.isna(r.demand_mwh) else float(r.demand_mwh),
          int(r.stop_work_site_days), int(r.warning_site_days), int(r.storm_events), float(r.crew_hours_lost),
          float(r.est_cost_usd), None if pd.isna(r.crew_trips) else int(r.crew_trips)) for r in keep.itertuples()])

    rows = []
    for ba, df in keep.groupby("ba"):
        correlate(df.sort_values("week"), X_METRICS + (["crew_trips"] if df["crew_trips"].notna().any() else []),
                  "demand_mwh", "weekly_ba", ba, rows)

    sales = frame(conn, "SELECT utility_id, utility_name, month, sum(revenue_thousand_usd) AS revenue_thousand_usd, "
                        "sum(sales_mwh) AS sales_mwh FROM utility_monthly_sales GROUP BY 1, 2, 3")
    if not sales.empty:
        site_util = frame(conn, "SELECT site_id, utility_id FROM sites WHERE utility_id IS NOT NULL")
        d = days.merge(site_util, on="site_id")
        d["month"] = pd.to_datetime(d["day"]).dt.to_period("M").dt.start_time
        m = d.groupby(["utility_id", "month"]).agg(
            stop_work_site_days=("level", lambda s: (s == "stop_work").sum()),
            heat_site_days=("category", lambda s: (s == "extreme_heat").sum())).reset_index()
        l2 = lost.merge(site_util, on="site_id")
        l2["month"] = pd.to_datetime(l2["day"]).dt.to_period("M").dt.start_time
        m = m.merge(l2.groupby(["utility_id", "month"])["hours"].sum().rename("crew_hours_lost").reset_index(),
                    how="left").fillna({"crew_hours_lost": 0})
        n_sites = site_util.groupby("utility_id").size()
        m["sites"] = m["utility_id"].map(n_sites)
        m["heat_site_days_per_site"] = m["heat_site_days"] / m["sites"]
        m["stop_work_site_days_per_site"] = m["stop_work_site_days"] / m["sites"]
        sales["month"] = pd.to_datetime(sales["month"])
        m = m.merge(sales, on=["utility_id", "month"])
        for uid, df in m[m["sites"] >= 3].groupby("utility_id"):
            name = df["utility_name"].iloc[0]
            df = df.sort_values("month").rename(columns={"month": "week"})  # correlate() reads the date column
            for y in ("sales_mwh", "revenue_thousand_usd"):
                correlate(df, ["heat_site_days_per_site", "stop_work_site_days_per_site", "crew_hours_lost"], y,
                          "monthly_utility", name, rows)

    conn.execute("TRUNCATE economic_correlations")
    conn.cursor().executemany("INSERT INTO economic_correlations (scope, entity, metric_x, metric_y, deseasonalized, r, "
                              "p_value, spearman, n) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
    return keep, pd.DataFrame(rows, columns=["scope", "entity", "x", "y", "deseasonalized", "r", "p", "spearman", "n"])


def main():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        weekly_df, corr = run(conn)
    print(f"economic_weekly: {len(weekly_df)} operator-weeks; "
          f"crew hours lost (assumption-based) {weekly_df['crew_hours_lost'].sum():,.0f}, "
          f"est. cost ${weekly_df['est_cost_usd'].sum():,.0f}")
    if corr.empty:
        print("no correlations (not enough overlapping data)")
        return
    sig = corr[(corr["deseasonalized"]) & (corr["p"] < 0.05)].sort_values("r", key=abs, ascending=False)
    print(f"{len(corr)} correlations computed; deseasonalized and p < 0.05: {len(sig)}")
    with pd.option_context("display.width", 160, "display.max_rows", 40):
        print(sig.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
