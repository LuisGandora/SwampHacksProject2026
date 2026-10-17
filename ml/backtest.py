"""Walk-forward backtest: for each test year, train only on earlier years, score that year as if live, and compare
against what actually happened (NOAA events, deaths, injuries, damage, OSHA weather injuries).

    python -m ml.backtest          # ~10-15 min; writes ml/models/backtest.json (served at GET /model/backtest)

Variants, all scored on the same test site-days:
  A0  v2 design (trained on measured weather), scored on measured weather: what earlier checks reported. Optimistic:
      at 6 PM nobody knows tomorrow's measured weather.
  A   v2 design scored on the day-ahead forecast: what users of v2 would really have seen.
  B   v3: trained and scored on the day-ahead forecast.
  C   v3 + NWS alerts issued by 6 PM the day before + severity weighting (production).
Metrics: AUC for any hazard / severe outcome / a death; calibration (mean predicted chance / observed rate);
share of severe site-days that landed in the top 10% of scores; Spearman(score, damage) on days with an event.
"""
import json

import numpy as np
import pandas as pd
import psycopg
from psycopg.rows import dict_row
from scipy import stats
from sklearn.metrics import roc_auc_score

from db.setup_db import database_url
from ml.hazard_model import CATEGORIES, FEATURES, MODEL_DIR, NWS_GROUPS, combine, load_training, new_model

FOLDS = [("2023", "2023-01-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"),
         ("2025-26", "2025-01-01", "2027-01-01")]
OBSERVED = ["max_temp_f", "min_temp_f", "max_heat_index_f", "precip_in", "rain_prior_6d_in", "max_gust_kt",
            "doy_sin", "doy_cos", "lat", "lon"]
AS_FORECAST = {"max_temp_f": "fc_max_temp_f", "min_temp_f": "fc_min_temp_f", "max_heat_index_f": "fc_max_heat_index_f",
               "precip_in": "fc_qpf_in", "max_gust_kt": "fc_max_gust_kt"}
NO_ALERTS = [f for f in FEATURES if not f.startswith("nws_")]
TREES = 300


def metrics(t, score, p_any):
    ev = t["y_any"] > 0
    auc = lambda y: round(float(roc_auc_score(y, score)), 3) if y.nunique() > 1 else None
    top = score >= np.quantile(score, 0.9)
    sev = t["y_severe"] > 0
    return {
        "auc_any_hazard": auc(t["y_any"]), "auc_severe_outcome": auc(sev.astype(int)),
        "auc_death": auc((t["deaths"] > 0).astype(int)),
        "calibration_any": round(float(p_any.mean() / t["y_any"].mean()), 2),
        "severe_days_in_top_10pct": round(float((sev & top).sum() / max(sev.sum(), 1)), 3),
        "spearman_damage_on_event_days": round(float(stats.spearmanr(score[ev], t.loc[ev, "damage"])[0]), 3),
        "test_site_days": int(len(t)), "severe_site_days": int(sev.sum()), "death_site_days": int((t["deaths"] > 0).sum()),
    }


def run(df):
    df = df.dropna(subset=["fc_max_temp_f"]).copy()
    df["y_any"] = df[[f"y_{c}" for c in CATEGORIES]].max(axis=1)
    results = []
    for name, start, end in FOLDS:
        tr = df[df["day"] < start]
        te = df[(df["day"] >= start) & (df["day"] < end)].copy()
        if te.empty or tr["y_any"].sum() == 0:
            continue
        print(f"fold {name}: train {len(tr):,} site-days (< {start}), test {len(te):,}", flush=True)
        fold = {"fold": name, "variants": {}}

        obs = tr.dropna(subset=["max_temp_f"])
        a = new_model(TREES).fit(obs[OBSERVED], obs["y_any"])
        p = a.predict_proba(te[OBSERVED])[:, 1]
        fold["variants"]["A0 v2, measured weather (optimistic)"] = metrics(te, p * 10, p)
        as_fc = te[OBSERVED].copy()
        for o, f in AS_FORECAST.items():
            as_fc[o] = te[f]
        p = a.predict_proba(as_fc)[:, 1]
        fold["variants"]["A  v2, day-ahead forecast"] = metrics(te, p * 10, p)

        b = new_model(TREES).fit(tr[NO_ALERTS], tr["y_any"])
        p = b.predict_proba(te[NO_ALERTS])[:, 1]
        fold["variants"]["B  v3 forecast-trained"] = metrics(te, p * 10, p)

        none = np.ones(len(te))
        for cat in CATEGORIES:
            none *= 1 - new_model(TREES).fit(tr[FEATURES], tr[f"y_{cat}"]).predict_proba(te[FEATURES])[:, 1]
        p_any = 1 - none
        p_sev = new_model(TREES).fit(tr[FEATURES], tr["y_severe"]).predict_proba(te[FEATURES])[:, 1]
        fold["variants"]["C  v3 + NWS alerts, P(any) only"] = metrics(te, p_any * 10, p_any)
        fold["variants"]["C  v3 + NWS alerts + severity (production)"] = metrics(te, combine(p_any, p_sev), p_any)
        for v, m in fold["variants"].items():
            print(f"  {v:45} AUC any {m['auc_any_hazard']}  severe {m['auc_severe_outcome']}  death {m['auc_death']}  "
                  f"cal {m['calibration_any']}  severe-in-top10% {m['severe_days_in_top_10pct']}  "
                  f"rho(damage) {m['spearman_damage_on_event_days']}", flush=True)
        results.append(fold)
    return results


def main():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        df = load_training(conn)
    results = run(df)
    variants = results[0]["variants"].keys() if results else []
    summary = {v: {k: round(float(np.mean([f["variants"][v][k] for f in results
                                           if f["variants"][v][k] is not None])), 3)
                   for k in ("auc_any_hazard", "auc_severe_outcome", "auc_death", "calibration_any",
                             "severe_days_in_top_10pct", "spearman_damage_on_event_days")}
               for v in variants}
    out = {"folds": results, "mean_over_folds": summary, "nws_groups": NWS_GROUPS,
           "note": "Walk-forward: each fold trains only on earlier years. A0 uses measured weather and is optimistic."}
    (MODEL_DIR / "backtest.json").write_text(json.dumps(out, indent=2))
    print("\nMean over folds:")
    for v, m in summary.items():
        print(f"  {v:45} {m}")


if __name__ == "__main__":
    main()
