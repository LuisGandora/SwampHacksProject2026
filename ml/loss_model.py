"""Potential-loss model: predict a site's expected outage burden (customer-hours without power) from weather,
then monetize it with a labeled assumption.

    python -m ml.loss_model train     # build the site-day table from Tiger, train, evaluate, save the model
    python -m ml.loss_model score     # score today ('now') and tomorrow ('next_day') into loss_scores

Why this target: unlike crew-dispatch dollars (no public ground truth), EAGLE-I customer-hours without power
is a real, measured weather-impact outcome. We predict it per site from the site's county, then multiply by
economic_assumptions.usd_per_customer_hour_out (a labeled assumption) for an estimated dollar figure. The ML
learns the physical impact from real data; the dollar conversion is transparent and tunable, never invented
by the model.

Label:    county_outages_daily.customer_hours_out for the site's county (0 when no outage recorded that day).
Features: the same forecastable weather the hazard model uses, so the same model scores tomorrow from the NWS
          forecast. The target is heavily right-skewed (hurricanes), so we train on log1p and invert with expm1.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from sklearn.metrics import mean_absolute_error
from scipy.stats import spearmanr
from xgboost import XGBRegressor

from db.setup_db import database_url
from ml.hazard_model import FORECAST_DAY, add_season

# Trained on observed daily weather (full 2021-26 history -> maximum label coverage). At score time the NWS
# day-ahead forecast is mapped onto these same names (FORECAST_RENAME), so tomorrow is scored from the forecast.
FEATURES = ["max_temp_f", "min_temp_f", "max_heat_index_f", "precip_in", "rain_prior_6d_in", "max_gust_kt",
            "doy_sin", "doy_cos", "lat", "lon"]
FORECAST_RENAME = {"fc_max_temp_f": "max_temp_f", "fc_min_temp_f": "min_temp_f", "fc_max_heat_index_f": "max_heat_index_f",
                   "fc_qpf_in": "precip_in", "fc_max_gust_kt": "max_gust_kt"}

MODEL_DIR = Path(__file__).resolve().parent / "models" / "loss"
MODEL_PATH = MODEL_DIR / "customer_hours_out.json"
METRICS_PATH = MODEL_DIR / "metrics.json"
MODEL_VERSION = "loss-outage-xgb-v1"
TRAIN_END = pd.Timestamp("2024-12-31")   # train on the past, test on 2025 (temporal split, never random)
DEFAULT_USD_PER_CUSTOMER_HOUR = 5.0      # labeled assumption, seeded into economic_assumptions below

DDL = """
CREATE TABLE IF NOT EXISTS loss_scores (
    site_id                 bigint NOT NULL REFERENCES sites,
    scored_for              timestamptz NOT NULL,
    horizon                 text NOT NULL CHECK (horizon IN ('now', 'next_day')),
    customer_hours_out_pred numeric NOT NULL,   -- predicted outage burden in the site's county (customers x hours)
    est_cost_usd            numeric,            -- customer_hours_out_pred x usd_per_customer_hour_out (assumption)
    model_version           text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, scored_for, horizon)
);
"""

SEED_ASSUMPTION = """
INSERT INTO economic_assumptions (key, value, unit, note) VALUES
    ('usd_per_customer_hour_out', %s, 'USD per customer-hour without power',
     'ASSUMPTION: rough value of lost load / restoration cost per customer-hour; replace with company data')
ON CONFLICT (key) DO NOTHING;
"""


def query(conn, sql, params=()):
    return pd.DataFrame(conn.execute(sql, params).fetchall())


def county_customers(conn):
    """Representative customers tracked per county (median over the record), plus a global fallback.

    Raw customer-hours is dominated by county size, so we model a per-customer rate and scale back by this count."""
    df = query(conn, "SELECT county_fips, percentile_cont(0.5) WITHIN GROUP (ORDER BY customers) AS c "
                     "FROM county_outages_daily WHERE customers > 0 GROUP BY county_fips")
    med = dict(zip(df["county_fips"], df["c"].astype(float)))
    return med, float(pd.Series(med).median())


def load_training(conn):
    """Site-day table: station weather joined to the site's county outage burden.

    Label is customer_hours_out / customers (hours without power per customer) so the model learns weather intensity,
    not which county is biggest; it is scaled back to customer-hours (and dollars) by the county customer count."""
    w = query(conn, "SELECT station_id, day, max_temp_f, min_temp_f, max_heat_index_f, precip_in, max_gust_kt "
                    "FROM weather_daily ORDER BY station_id, day")
    w["day"] = pd.to_datetime(w["day"])
    for c in ("max_temp_f", "min_temp_f", "max_heat_index_f", "precip_in", "max_gust_kt"):
        w[c] = w[c].astype(float)
    w["rain_prior_6d_in"] = (w.set_index("day").groupby("station_id")["precip_in"]
                             .rolling("6D", closed="left").sum().reset_index(level=0, drop=True).values)

    sites = query(conn, "SELECT site_id, weather_station_id AS station_id, county_fips, "
                        "ST_Y(geog::geometry) AS lat, ST_X(geog::geometry) AS lon "
                        "FROM sites WHERE weather_station_id IS NOT NULL AND county_fips IS NOT NULL")
    df = sites.merge(w, on="station_id")

    out = query(conn, "SELECT county_fips, day, customer_hours_out, customers FROM county_outages_daily")
    out["day"] = pd.to_datetime(out["day"])
    out["customer_hours_out"] = out["customer_hours_out"].astype(float)
    out["customers"] = out["customers"].astype(float)
    df = df.merge(out, on=["county_fips", "day"], how="left")
    # A missing county-day in EAGLE-I means no recorded outage: treat as 0 so calm weather teaches a ~0 burden.
    df["customer_hours_out"] = df["customer_hours_out"].fillna(0.0)

    med, glob = county_customers(conn)
    df["cust"] = df["customers"].fillna(df["county_fips"].map(med)).fillna(glob)          # for scaling back
    df["rate"] = df["customer_hours_out"] / df["cust"]                                    # the model's target
    df.loc[df["customer_hours_out"] == 0, "rate"] = 0.0
    df = df[df["rate"].notna()]
    return add_season(df)


def new_model():
    # Tweedie: built for zero-inflated, right-skewed non-negative targets (outages behave like insurance claims).
    # It predicts the expected rate directly through a log link, so no manual log1p is needed.
    return XGBRegressor(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                        min_child_weight=5, tree_method="hist", objective="reg:tweedie",
                        tweedie_variance_power=1.5, n_jobs=-1, random_state=42)


def train(conn):
    df = load_training(conn)
    train_mask = df["day"] <= TRAIN_END
    n_tr, n_te = int(train_mask.sum()), int((~train_mask).sum())
    print(f"{len(df):,} site-days ({n_tr:,} train <= {TRAIN_END.date()}, {n_te:,} test)")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    m = new_model().fit(df.loc[train_mask, FEATURES], df.loc[train_mask, "rate"])

    # evaluate end-to-end in customer-hours: predicted rate x county customers vs the actual customer_hours_out
    pred = m.predict(df.loc[~train_mask, FEATURES]).clip(min=0) * df.loc[~train_mask, "cust"].values
    yt = df.loc[~train_mask, "customer_hours_out"].values
    order = np.argsort(pred)
    top10 = order[-len(order) // 10:]   # the 10% of site-days the model flags as highest risk
    metrics = {
        "version": MODEL_VERSION, "features": FEATURES, "target": "customer_hours_out/customers (scaled back)",
        "trained_at": dt.datetime.now().isoformat(), "rows": int(len(df)), "train_rows": n_tr, "test_rows": n_te,
        "test_mean_actual": round(float(yt.mean()), 1),
        "test_mae": round(float(mean_absolute_error(yt, pred)), 1),
        # rank quality is the headline for a risk-ranking tool (robust to the unpredictable hurricane tail):
        "test_spearman": round(float(spearmanr(yt, pred).statistic), 3),
        # is the total predicted loss unbiased in aggregate (so dollar estimates are sound on average)?
        "test_total_pred_over_actual": round(float(pred.sum() / yt.sum()), 3),
        # what share of all real outage customer-hours fell in the model's top-10% riskiest days?
        "test_top10pct_capture": round(float(yt[top10].sum() / yt.sum()), 3),
    }
    print(f"  test MAE {metrics['test_mae']:,} customer-h | Spearman {metrics['test_spearman']} | "
          f"aggregate pred/actual {metrics['test_total_pred_over_actual']} | "
          f"top-10% risk days capture {metrics['test_top10pct_capture']:.0%} of real outage-hours")

    # production model: same settings, refit on all years
    prod = new_model().fit(df[FEATURES], df["rate"])
    prod.save_model(MODEL_PATH)
    gain = prod.get_booster().get_score(importance_type="gain")
    total = sum(gain.values()) or 1
    metrics["feature_gain_share"] = {f: round(g / total, 3) for f, g in sorted(gain.items(), key=lambda kv: -kv[1])}
    print("  feature gain share:", metrics["feature_gain_share"])
    METRICS_PATH.write_text(json.dumps(metrics, indent=2))
    return metrics


def load_model():
    m = XGBRegressor()
    m.load_model(MODEL_PATH)
    return m


def score(conn):
    """Score today and tomorrow for every site from the NWS forecast, write loss_scores."""
    conn.execute(DDL)
    conn.execute(SEED_ASSUMPTION, (DEFAULT_USD_PER_CUSTOMER_HOUR,))
    usd = float(conn.execute("SELECT value FROM economic_assumptions WHERE key = 'usd_per_customer_hour_out'")
                .fetchone()["value"])
    med, glob = county_customers(conn)
    site_county = {r["site_id"]: r["county_fips"]
                   for r in conn.execute("SELECT site_id, county_fips FROM sites WHERE county_fips IS NOT NULL")}
    model = load_model()
    written = 0
    for horizon, offset in (("now", 0), ("next_day", 1)):
        df = query(conn, FORECAST_DAY, {"offset": offset})
        if df.empty:
            continue
        df = df.rename(columns=FORECAST_RENAME)  # forecast columns -> the observed-weather names the model trained on
        for c in ("max_temp_f", "min_temp_f", "max_heat_index_f", "precip_in", "max_gust_kt", "rain_prior_6d_in"):
            df[c] = df[c].astype(float).fillna(0)
        df = add_season(df)
        rate = np.expm1(model.predict(df[FEATURES])).clip(min=0)   # predicted customer-hours per customer
        cust = df["site_id"].map(lambda s: med.get(site_county.get(int(s)), glob)).astype(float).values
        pred = rate * cust                                         # scaled back to county customer-hours
        rows = [(int(r.site_id), r.day, r.timezone, horizon, round(float(p), 1), round(float(p) * usd, 2), MODEL_VERSION)
                for r, p in zip(df.itertuples(), pred)]
        conn.cursor().executemany(
            "INSERT INTO loss_scores (site_id, scored_for, horizon, customer_hours_out_pred, est_cost_usd, "
            "model_version) VALUES (%s, (%s::date)::timestamp AT TIME ZONE %s, %s, %s, %s, %s) "
            "ON CONFLICT (site_id, scored_for, horizon) DO UPDATE SET "
            "customer_hours_out_pred = EXCLUDED.customer_hours_out_pred, est_cost_usd = EXCLUDED.est_cost_usd, "
            "model_version = EXCLUDED.model_version, created_at = now()", rows)
        written += len(rows)
    return f"{written} loss scores written (usd_per_customer_hour_out = {usd})"


def main():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        if sys.argv[1:] == ["train"]:
            train(conn)
        elif sys.argv[1:] == ["score"]:
            print(score(conn))
        else:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
