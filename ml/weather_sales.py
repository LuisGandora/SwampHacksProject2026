"""Weather measured in real sales: how many MWh and dollars of each utility's actual monthly sales were weather.

    python -m ml.weather_sales        # after ml.economic_impact (needs utility_monthly_sales and weather_daily)

Utilities report a "weather" line in their 10-Q revenue tables using degree days; this does the same with public
data. Per utility and state where we have sites:

    sales_mwh(month) = a + b_cdd * CDD + b_hdd * HDD + b_trend * month_index + b_days * days_in_month

CDD/HDD (cooling/heating degree days, base 65 F) come from the daily mean temperature at the stations of that
utility's sites in that state (weather_daily). Sales are EIA-861M retail sales, real reported numbers. The weather
part of a month is b_cdd * (CDD - normal CDD) + b_hdd * (HDD - normal HDD), with "normal" the average of that
calendar month over the years we have; weather_revenue_usd prices it at that month's average retail price.
The fit is checked against the same model without weather (trend and days only): the R^2 gain is how much of the
month-to-month swing in sales is weather.
"""
import datetime as dt

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row

from db.setup_db import database_url

BASE_F = 65
MIN_DAYS = 25      # weather days a month needs to count
MIN_MONTHS = 30

TEMPS = """
SELECT s.utility_id, s.state, w.day, avg((w.max_temp_f + w.min_temp_f) / 2) AS mean_f
  FROM (SELECT DISTINCT utility_id, state, weather_station_id FROM sites
         WHERE utility_id IS NOT NULL AND weather_station_id IS NOT NULL) s
  JOIN weather_daily w ON w.station_id = s.weather_station_id
 WHERE w.max_temp_f IS NOT NULL AND w.min_temp_f IS NOT NULL
 GROUP BY 1, 2, 3
"""


def fit(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return beta, 1 - resid.var() / y.var()


def run(conn):
    t = pd.DataFrame(conn.execute(TEMPS).fetchall())
    t["mean_f"] = t["mean_f"].astype(float)
    t["month"] = pd.to_datetime(t["day"]).dt.to_period("M").dt.start_time
    t["cdd"] = (t["mean_f"] - BASE_F).clip(lower=0)
    t["hdd"] = (BASE_F - t["mean_f"]).clip(lower=0)
    dd = t.groupby(["utility_id", "state", "month"]).agg(cdd=("cdd", "sum"), hdd=("hdd", "sum"), days=("day", "count")).reset_index()
    dd["days_in_month"] = dd["month"].dt.days_in_month
    dd = dd[dd["days"] >= MIN_DAYS]
    # scale partly covered months up to the whole month
    for c in ("cdd", "hdd"):
        dd[c] = dd[c] * dd["days_in_month"] / dd["days"]
    sales = pd.DataFrame(conn.execute("SELECT utility_id, utility_name, state, month, sales_mwh, revenue_thousand_usd "
                                      "FROM utility_monthly_sales WHERE sales_mwh > 0").fetchall())
    sales["month"] = pd.to_datetime(sales["month"])
    m = sales.merge(dd, on=["utility_id", "state", "month"])
    models, effects = [], []
    for (uid, state), g in m.groupby(["utility_id", "state"]):
        if len(g) < MIN_MONTHS:
            continue
        g = g.sort_values("month").copy()
        y = g["sales_mwh"].astype(float).to_numpy()
        idx = ((g["month"].dt.year - 2020) * 12 + g["month"].dt.month).to_numpy(float)
        base = np.column_stack([np.ones(len(g)), idx, g["days_in_month"].to_numpy(float)])
        X = np.column_stack([base, g["cdd"], g["hdd"]])
        beta, r2 = fit(X, y)
        _, r2_base = fit(base, y)
        normal = g.groupby(g["month"].dt.month)[["cdd", "hdd"]].transform("mean")
        g["weather_mwh"] = beta[3] * (g["cdd"] - normal["cdd"]) + beta[4] * (g["hdd"] - normal["hdd"])
        g["price"] = g["revenue_thousand_usd"].astype(float) * 1000 / g["sales_mwh"].astype(float)
        g["weather_revenue_usd"] = g["weather_mwh"] * g["price"]
        name = g["utility_name"].iloc[0]
        models.append((uid, state, name, len(g), round(r2, 3), round(r2_base, 3), round(beta[3], 1), round(beta[4], 1),
                       g["month"].min().date(), g["month"].max().date()))
        effects += [(uid, state, name, r.month.date(), float(r.sales_mwh), round(r.cdd, 1), round(r.hdd, 1),
                     round(r.weather_mwh), round(r.weather_revenue_usd)) for r in g.itertuples()]
    conn.execute("TRUNCATE weather_sales_model, weather_sales_effect")
    conn.cursor().executemany("INSERT INTO weather_sales_model (utility_id, state, utility_name, months, r2, r2_without_weather, "
                              "mwh_per_cdd, mwh_per_hdd, first_month, last_month) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", models)
    conn.cursor().executemany("INSERT INTO weather_sales_effect (utility_id, state, utility_name, month, sales_mwh, cdd, hdd, "
                              "weather_mwh, weather_revenue_usd) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", effects)
    return models, effects


def main():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        models, effects = run(conn)
    e = pd.DataFrame(effects, columns=["uid", "state", "name", "month", "sales", "cdd", "hdd", "mwh", "usd"])
    e["year"] = pd.to_datetime(e["month"]).dt.year
    print(f"{len(models)} utility-state models, {len(effects)} months  (computed {dt.date.today()})")
    for uid, state, name, n, r2, r2b, bc, bh, first, last in models:
        yr = e[(e.uid == uid) & (e.state == state)].groupby("year")["usd"].sum() / 1e6
        print(f"  {name} ({state}): R2 {r2b} -> {r2} with weather, {bc:,.0f} MWh per CDD, {bh:,.0f} per HDD, n={n}; "
              "weather $M by year: " + ", ".join(f"{y}: {v:+.0f}" for y, v in yr.items()))


if __name__ == "__main__":
    main()
