"""Hazard Index model v3: one row per site per day, trained on what was *forecast* the day before.

    python -m ml.hazard_model train     # build the site-day table, train, evaluate on 2025-26, save models
    python -m ml.hazard_model score     # score today ('now') and tomorrow ('next_day') into hazard_scores
    python -m ml.backtest               # walk-forward backtest (see ml/backtest.py)

Inputs (all known by 6 PM the evening before, so training matches scoring):
  - NBM day-ahead forecast at the site's station: temperature, heat index, hours at heat index >= 103 F (crew heat
    risk), rain amount and chance, thunder chance, gusts, wind (scraper/past_forecasts.py)
  - NWS watches/warnings/advisories issued by 6 PM the day before that cover the site's zone, by group
    (scraper/nws_warning_history.py; live: the nws_alerts table)
  - observed rain over the previous 6 days, season, location
Labels: did a recorded hazard happen within the category's radius that day (NOAA storm events + OSHA weather
injuries, the site_hazard_events ties); "severe": an event that killed or injured someone, did >= $1M damage, or an
OSHA weather injury to a worker.

Hazard Index = 10 x (1 - (1 - P_any) x (1 - P_severe) ^ SEVERITY_WEIGHT), where P_any = 1 - prod(1 - p_category).
A severe outcome counts SEVERITY_WEIGHT times as much as an ordinary hazard event (a stated assumption).
Live adjustments (active alerts, crew reports) are applied on top at read time, see the site_live_floor view.
"""
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg
import xgboost as xgb
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from xgboost import XGBClassifier

from db.setup_db import database_url

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODEL_DIR = Path(__file__).resolve().parent / "models"
CATEGORIES = ["severe_storm", "tropical", "flooding", "lightning", "extreme_heat", "extreme_cold", "high_wind"]
NWS_GROUPS = ["heat", "flood", "wind", "tropical", "cold", "fire", "fog", "convective"]
FC = ["fc_max_temp_f", "fc_min_temp_f", "fc_max_heat_index_f", "fc_heat_hours_103", "fc_qpf_in", "fc_max_pop",
      "fc_max_thunder", "fc_max_gust_kt", "fc_max_wind_kt"]
FEATURES = FC + ["rain_prior_6d_in"] + [f"nws_{g}" for g in NWS_GROUPS] + ["doy_sin", "doy_cos", "lat", "lon"]
WEATHER_DRIVERS = set(FC) | {"rain_prior_6d_in"} | {f"nws_{g}" for g in NWS_GROUPS}
SEVERITY_WEIGHT = 4
TRAIN_END = pd.Timestamp("2024-12-31")
MODEL_VERSION = "site-day-xgb-v3"
LABELS = {
    "fc_max_temp_f": "forecast high", "fc_min_temp_f": "forecast low", "fc_max_heat_index_f": "forecast heat index",
    "fc_heat_hours_103": "hours at heat index 103+", "fc_qpf_in": "forecast rain (in)", "fc_max_pop": "chance of rain",
    "fc_max_thunder": "chance of thunder", "fc_max_gust_kt": "forecast gusts (kt)", "fc_max_wind_kt": "forecast wind (kt)",
    "rain_prior_6d_in": "rain over the previous 6 days", **{f"nws_{g}": f"NWS {g} alert" for g in NWS_GROUPS},
    "doy_sin": "time of year", "doy_cos": "time of year", "lat": "location", "lon": "location",
}


def query(conn, sql, params=()):
    return pd.DataFrame(conn.execute(sql, params).fetchall())


def add_season(df):
    doy = pd.to_datetime(df["day"]).dt.dayofyear
    df["doy_sin"], df["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    return df


def nws_event_group(event):
    """NWS alert name (live API) -> the same groups as the VTEC history. None for statements we don't model."""
    e = (event or "").lower()
    if "statement" in e or "outlook" in e or "rip current" in e or "surf" in e:
        return None
    for group, words in (("cold", ("wind chill", "freeze", "frost", "cold", "winter", "ice storm", "blizzard")),
                         ("heat", ("heat",)), ("tropical", ("tropical", "hurricane", "storm surge")),
                         ("flood", ("flood",)), ("wind", ("wind",)), ("fire", ("red flag", "fire weather")),
                         ("fog", ("fog", "smoke")), ("convective", ("tornado watch", "severe thunderstorm watch"))):
        if any(w in e for w in words):
            return group
    return None


def nws_history_days(sites):
    """{(site_id, day): set(groups)} from the VTEC archive: alerts covering the site's zone/county on day D that were
    issued before 6 PM local on D-1 (what the evening briefing could have known)."""
    path = DATA / "nws_warning_history.csv"
    if not path.exists():
        return {}
    by_ugc = {}
    for s in sites.itertuples():
        for ugc in (s.forecast_zone, s.county_ugc):
            if ugc:
                by_ugc.setdefault(ugc, []).append((s.site_id, ZoneInfo(s.timezone)))
    out = {}
    for r in pd.read_csv(path).itertuples():
        targets = by_ugc.get(r.ugc)
        if not targets:
            continue
        issued = dt.datetime.fromisoformat(r.issued_utc).replace(tzinfo=dt.timezone.utc)
        expires = dt.datetime.fromisoformat(r.expires_utc).replace(tzinfo=dt.timezone.utc)
        for site_id, tz in targets:
            first = (issued.astimezone(tz) + dt.timedelta(hours=6)).date() + dt.timedelta(days=1)
            last = (expires.astimezone(tz) - dt.timedelta(minutes=1)).date()
            d = first
            while d <= last:
                out.setdefault((site_id, d), set()).add(r.group)
                d += dt.timedelta(days=1)
    return out


def add_nws(df, days):
    for g in NWS_GROUPS:
        df[f"nws_{g}"] = 0
    if days:
        keys = list(zip(df["site_id"], pd.to_datetime(df["day"]).dt.date))
        for g in NWS_GROUPS:
            df[f"nws_{g}"] = [int(g in days.get(k, ())) for k in keys]
    return df


def load_training(conn):
    """Site-day table: forecast + alert features, observed weather (for checks), and labels."""
    w = query(conn, "SELECT station_id, day, max_temp_f, min_temp_f, max_heat_index_f, precip_in, max_gust_kt "
                    "FROM weather_daily ORDER BY station_id, day")
    w["day"] = pd.to_datetime(w["day"])
    for c in ["max_temp_f", "min_temp_f", "max_heat_index_f", "precip_in", "max_gust_kt"]:
        w[c] = w[c].astype(float)
    w["rain_prior_6d_in"] = (w.set_index("day").groupby("station_id")["precip_in"]
                             .rolling("6D", closed="left").sum().reset_index(level=0, drop=True).values)
    sites = query(conn, "SELECT site_id, weather_station_id AS station_id, timezone, forecast_zone, county_ugc, "
                        "ST_Y(geog::geometry) AS lat, ST_X(geog::geometry) AS lon FROM sites "
                        "WHERE weather_station_id IS NOT NULL")
    df = sites.merge(w, on="station_id")
    nbs = DATA / "nbs_daily_forecasts.csv"
    if nbs.exists():
        f = pd.read_csv(nbs).rename(columns={"station": "station_id"})
        f["day"] = pd.to_datetime(f["day"])
        df = df.merge(f, on=["station_id", "day"], how="left")
    else:
        for c in FC:
            df[c] = np.nan
    df = add_nws(df, nws_history_days(sites))

    labels = query(conn, """
        SELECT h.site_id, h.category, (h.event_time AT TIME ZONE s.timezone)::date AS day, count(*) AS n,
               sum(h.deaths) AS deaths, sum(h.injuries) FILTER (WHERE h.source = 'noaa_storm_event') AS storm_injuries,
               count(*) FILTER (WHERE h.source = 'osha_injury') AS osha, coalesce(sum(h.damage_usd), 0) AS damage
          FROM site_hazard_events h JOIN sites s USING (site_id)
         WHERE h.category = ANY (%s) AND h.event_time >= (SELECT min(day) FROM weather_daily)
         GROUP BY 1, 2, 3""", (CATEGORIES,))
    labels["day"] = pd.to_datetime(labels["day"])
    y = labels.pivot_table(index=["site_id", "day"], columns="category", values="n", aggfunc="sum").reindex(
        columns=CATEGORIES).fillna(0).gt(0).astype(int).add_prefix("y_").reset_index()
    sev = labels.groupby(["site_id", "day"]).agg(deaths=("deaths", "sum"), storm_injuries=("storm_injuries", "sum"),
                                                 osha=("osha", "sum"), damage=("damage", "sum")).reset_index()
    for c in ("deaths", "storm_injuries", "osha", "damage"):
        sev[c] = sev[c].astype(float).fillna(0)
    sev["y_severe"] = ((sev.deaths > 0) | (sev.storm_injuries > 0) | (sev.osha > 0) | (sev.damage >= 1e6)).astype(int)
    df = df.merge(y, on=["site_id", "day"], how="left").merge(sev, on=["site_id", "day"], how="left")
    ycols = [f"y_{c}" for c in CATEGORIES] + ["y_severe"]
    df[ycols] = df[ycols].fillna(0).astype(int)
    df[["deaths", "storm_injuries", "osha", "damage"]] = df[["deaths", "storm_injuries", "osha", "damage"]].fillna(0)
    return add_season(df)


def new_model(n_estimators=400):
    # No class weighting: probabilities should mean what they say (calibration is checked in the answer key).
    return XGBClassifier(n_estimators=n_estimators, max_depth=5, learning_rate=0.05, subsample=0.8,
                         colsample_bytree=0.8, min_child_weight=5, tree_method="hist", eval_metric="logloss",
                         n_jobs=-1, random_state=42)


def evaluate(y, p):
    return {"base_rate": round(float(y.mean()), 5), "positives_test": int(y.sum()),
            "roc_auc": round(float(roc_auc_score(y, p)), 3) if y.nunique() > 1 else None,
            "pr_auc": round(float(average_precision_score(y, p)), 3) if y.nunique() > 1 else None,
            "brier": round(float(brier_score_loss(y, p)), 5), "mean_predicted": round(float(p.mean()), 5),
            "calibration_ratio": round(float(p.mean() / y.mean()), 2) if y.mean() > 0 else None}


def train(conn):
    df = load_training(conn)
    df = df.dropna(subset=["fc_max_temp_f"])  # only days with a stored day-ahead forecast
    train_mask = df["day"] <= TRAIN_END
    print(f"{len(df):,} site-days with forecasts ({train_mask.sum():,} train, {(~train_mask).sum():,} test 2025-26)")
    MODEL_DIR.mkdir(exist_ok=True)
    metrics = {"version": MODEL_VERSION, "features": FEATURES, "severity_weight": SEVERITY_WEIGHT,
               "trained_at": dt.datetime.now().isoformat(), "rows": int(len(df)), "categories": {}}
    for target in CATEGORIES + ["severe"]:
        y = df[f"y_{target}"]
        m = new_model().fit(df.loc[train_mask, FEATURES], y[train_mask])
        res = evaluate(y[~train_mask], m.predict_proba(df.loc[~train_mask, FEATURES])[:, 1])
        metrics["categories"][target] = res
        print(f"  {target:13} test AUC {res['roc_auc']}  PR-AUC {res['pr_auc']}  base {res['base_rate']:.4f}  "
              f"n+ {res['positives_test']}  pred/obs {res['calibration_ratio']}")
        new_model().fit(df[FEATURES], y).save_model(MODEL_DIR / f"{target}.json")  # production: all years
    gain = {}
    for target in CATEGORIES + ["severe"]:
        for f, g in load_model(target).get_booster().get_score(importance_type="gain").items():
            gain[f] = gain.get(f, 0) + g
    total = sum(gain.values())
    metrics["feature_gain_share"] = {f: round(g / total, 3) for f, g in sorted(gain.items(), key=lambda kv: -kv[1])}
    print("  feature gain share:", dict(list(metrics["feature_gain_share"].items())[:8]))
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    save_climatology(conn, predict(df.copy(), drivers=False))
    return metrics


def save_climatology(conn, scored):
    """Each site's normal range by calendar month (percentiles 0-100 of its daily index), for 'unusual for this site'."""
    scored["month"] = pd.to_datetime(scored["day"]).dt.month
    rows = [(int(s), int(m), [round(float(q), 3) for q in np.percentile(g["score"], np.arange(101))])
            for (s, m), g in scored.groupby(["site_id", "month"])]
    conn.execute("TRUNCATE site_score_climatology")
    conn.cursor().executemany("INSERT INTO site_score_climatology VALUES (%s, %s, %s)", rows)
    conn.commit()
    print(f"  climatology: {len(rows)} site-months")


def load_model(target):
    m = XGBClassifier()
    m.load_model(MODEL_DIR / f"{target}.json")
    return m


def combine(p_any, p_severe):
    return np.round((1 - (1 - p_any) * (1 - p_severe) ** SEVERITY_WEIGHT) * 10, 1)


def predict(df, models=None, drivers=True):
    """Adds p_<category>, p_any, p_severe, score and (optionally) category_scores with top weather drivers."""
    models = models or {t: load_model(t) for t in CATEGORIES + ["severe"]}
    X = df[FEATURES].astype(float)
    none = np.ones(len(df))
    breakdown = [dict() for _ in range(len(df))] if drivers else None
    for cat in CATEGORIES:
        p = models[cat].predict_proba(X)[:, 1]
        df[f"p_{cat}"] = p
        none *= 1 - p
        if drivers:
            contrib = models[cat].get_booster().predict(xgb.DMatrix(X), pred_contribs=True)[:, :-1]
            for i in range(len(df)):
                top = sorted(((contrib[i, j], FEATURES[j]) for j in range(len(FEATURES))
                              if FEATURES[j] in WEATHER_DRIVERS and not np.isnan(X.iat[i, j])), reverse=True)[:3]
                breakdown[i][cat] = {"score": round(float(p[i]) * 10, 1),
                                     "drivers": [{"feature": f, "label": LABELS[f], "value": round(float(X.iloc[i][f]), 2)}
                                                 for c, f in top if c > 0]}
    df["p_any"] = 1 - none
    df["p_severe"] = models["severe"].predict_proba(X)[:, 1]
    df["score"] = combine(df["p_any"].values, df["p_severe"].values)
    if drivers:
        df["category_scores"] = breakdown
    return df


def expected_crew_cost(df, a):
    """sum(p_category x hours_lost_<category>), capped at one shift, x crew size (x cost). Assumption-based."""
    hours = sum(df[f"p_{c}"] * a.get(f"hours_lost_{c}", 0.0) for c in CATEGORIES).clip(upper=a.get("shift_hours", 10.0))
    df["expected_crew_hours_lost"] = (hours * a.get("crew_size", 0.0)).round(2)
    rate = df["state"].map(lambda st: a.get(f"loaded_cost_per_hour_{st}", a.get("loaded_cost_per_hour", 0.0)))
    df["expected_cost_usd"] = (df["expected_crew_hours_lost"] * rate).round(0)  # state labor rate (BLS) when loaded
    return df


# Live features for day D (today or tomorrow, site local time): the NWS gridpoint forecast for the site (hours
# already observed today replace the forecast), and the 6 previous days of observed rain.
FORECAST_DAY = """
WITH s AS (SELECT site_id, timezone, state, weather_station_id AS st, geog,
                  (now() AT TIME ZONE timezone)::date AS today FROM sites),
fc AS (
    SELECT f.site_id, date_trunc('hour', f.valid_at) AS h, max(f.temp_f) AS t,
           max(coalesce(f.heat_index_f, f.temp_f)) AS hi, max(f.precip_in) AS p, max(f.wind_gust_mph) AS g,
           max(f.wind_mph) AS w, max(f.precip_chance) AS pop, max(f.thunder_chance) AS thunder
      FROM weather_forecasts f JOIN s USING (site_id)
     WHERE f.valid_at >= s.today::timestamp AT TIME ZONE s.timezone
       AND f.valid_at < (s.today + 2)::timestamp AT TIME ZONE s.timezone
     GROUP BY 1, 2
), ob AS (
    SELECT o.site_id, date_trunc('hour', o.observed_at) AS h, max(o.temp_f) AS t,
           max(coalesce(o.heat_index_f, o.temp_f)) AS hi, max(o.precip_1h_in) AS p, max(o.wind_gust_mph) AS g,
           max(o.wind_mph) AS w
      FROM weather_observations o JOIN s USING (site_id)
     WHERE o.observed_at >= s.today::timestamp AT TIME ZONE s.timezone AND o.observed_at < now()
     GROUP BY 1, 2
), hrs AS (
    SELECT coalesce(ob.site_id, fc.site_id) AS site_id, coalesce(ob.h, fc.h) AS h, coalesce(ob.t, fc.t) AS t,
           coalesce(ob.hi, fc.hi) AS hi, coalesce(ob.p, fc.p) AS p, coalesce(ob.g, fc.g) AS g,
           coalesce(ob.w, fc.w) AS w, fc.pop, fc.thunder
      FROM fc FULL JOIN ob ON ob.site_id = fc.site_id AND ob.h = fc.h
), days AS (
    SELECT x.site_id, (x.h AT TIME ZONE s.timezone)::date AS day, max(x.t) AS fc_max_temp_f,
           min(x.t) AS fc_min_temp_f, max(x.hi) AS fc_max_heat_index_f,
           count(*) FILTER (WHERE x.hi >= 103) AS fc_heat_hours_103, coalesce(sum(x.p), 0) AS fc_qpf_in,
           max(x.pop) AS fc_max_pop, max(x.thunder) AS fc_max_thunder, max(x.g) / 1.15078 AS fc_max_gust_kt,
           max(x.w) / 1.15078 AS fc_max_wind_kt, count(*) AS hours
      FROM hrs x JOIN s USING (site_id) GROUP BY 1, 2
)
SELECT s.site_id, s.timezone, s.state, ST_Y(s.geog::geometry) AS lat, ST_X(s.geog::geometry) AS lon, d.day,
       d.fc_max_temp_f, d.fc_min_temp_f, d.fc_max_heat_index_f, d.fc_heat_hours_103, d.fc_qpf_in, d.fc_max_pop,
       d.fc_max_thunder, d.fc_max_gust_kt, d.fc_max_wind_kt, d.hours,
       coalesce((SELECT sum(w.precip_in) FROM weather_daily w WHERE w.station_id = s.st
                  AND w.day >= d.day - 6 AND w.day < least(d.day, s.today)), 0)
     + coalesce((SELECT sum(x.fc_qpf_in) FROM days x WHERE x.site_id = d.site_id
                  AND x.day >= s.today AND x.day < d.day), 0) AS rain_prior_6d_in
  FROM days d JOIN s USING (site_id)
 WHERE d.day = s.today + %(offset)s
"""

LIVE_ALERTS = """
SELECT s.site_id, a.event
  FROM nws_alerts a JOIN sites s ON CASE WHEN a.geog IS NOT NULL THEN ST_Covers(a.geog, s.geog)
                                          ELSE a.ugc_codes && ARRAY[s.forecast_zone, s.county_ugc] END
 WHERE a.expires_at > (((now() AT TIME ZONE s.timezone)::date + %(offset)s)::timestamp AT TIME ZONE s.timezone)
   AND coalesce(a.onset_at, a.sent_at) < (((now() AT TIME ZONE s.timezone)::date + %(offset)s + 1)::timestamp
                                           AT TIME ZONE s.timezone)
   AND a.sent_at < (((now() AT TIME ZONE s.timezone)::date + %(offset)s - 1)::timestamp AT TIME ZONE s.timezone)
                   + interval '18 hours'
"""


def score(conn):
    """Score today and tomorrow for every site, write hazard_scores (with severity and 'unusual' percentile)."""
    models = {t: load_model(t) for t in CATEGORIES + ["severe"]}
    assumptions = {r["key"]: float(r["value"]) for r in conn.execute("SELECT key, value FROM economic_assumptions")}
    clim = {(r["site_id"], r["month"]): np.array(r["q"], dtype=float)
            for r in conn.execute("SELECT site_id, month, q FROM site_score_climatology")}
    written = 0
    for horizon, offset in (("now", 0), ("next_day", 1)):
        df = query(conn, FORECAST_DAY, {"offset": offset})
        if df.empty:
            continue
        for c in FC + ["rain_prior_6d_in"]:
            df[c] = df[c].astype(float)
        live = {}
        for r in conn.execute(LIVE_ALERTS, {"offset": offset}):
            g = nws_event_group(r["event"])
            if g:
                live.setdefault(r["site_id"], set()).add(g)
        for g in NWS_GROUPS:
            df[f"nws_{g}"] = [int(g in live.get(s, ())) for s in df["site_id"]]
        df = expected_crew_cost(predict(add_season(df), models), assumptions)
        rows = []
        for r in df.itertuples():
            q = clim.get((int(r.site_id), pd.Timestamp(r.day).month))
            pct = float(np.searchsorted(q, r.score, side="right") - 1) if q is not None else None
            rows.append((int(r.site_id), r.day, r.timezone, horizon, float(r.score), Jsonb({**r.category_scores, "_meta": {
                "p_any": round(float(r.p_any), 4), "p_severe": round(float(r.p_severe), 4),
                "severity_weight": SEVERITY_WEIGHT, "percentile_for_site_month": pct,
                "nws_alerts": sorted(live.get(int(r.site_id), ())), "forecast_hours": int(r.hours),
                "expected_crew_hours_lost": float(r.expected_crew_hours_lost),
                "expected_cost_usd": float(r.expected_cost_usd),
                "economic_note": "estimate from economic_assumptions (labeled assumptions)"}}), MODEL_VERSION))
        conn.cursor().executemany(
            "INSERT INTO hazard_scores (site_id, scored_for, horizon, score, category_scores, model_version) "
            "VALUES (%s, (%s::date)::timestamp AT TIME ZONE %s, %s, %s, %s, %s) "
            "ON CONFLICT (site_id, scored_for, horizon) DO UPDATE SET score = EXCLUDED.score, "
            "category_scores = EXCLUDED.category_scores, model_version = EXCLUDED.model_version, created_at = now()",
            rows)
        written += len(rows)
    return f"{written} scores written"


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
