"""Train a gradient-boosted Hazard Index model for power plants.

Pipeline:
  1. Load and join the per-plant feature files.
  2. Build a real-outcome target (storm casualties) to train on.
  3. Train an XGBoost regressor on weather/climate features.
  4. Evaluate, then map predictions to a 0-10 Hazard Index.
  5. Report feature importances and write the scores to disk.

Inputs (one row per plant):
  data/plant_weather_summary.csv   storm casualties, OSHA/CDC counts, capacity, technology
  data/plant_stations.csv          climate frequencies (heat / heavy-rain / high-gust days per year)

Output:
  data/hazard_scores.csv           plant_id, name, state, 0-10 hazard_index
"""
from pathlib import Path

import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
from xgboost import XGBRegressor

DATA = Path("data")
RANDOM_STATE = 42

# Weather/climate inputs. We deliberately exclude storm_deaths/storm_injuries here
# because they define the target below (using them as features would be leakage).
CLIMATE_FEATURES = [
    "heat_index_105f_days_per_year",
    "rain_2in_days_per_year",
    "gust_40kt_days_per_year",
    "capacity_mw",
]
CATEGORICAL_FEATURES = ["primary_technology", "state"]


def load_features():
    """Join the two per-plant files into one feature table (one row per plant)."""
    summary = pd.read_csv(DATA / "plant_weather_summary.csv")
    stations = pd.read_csv(DATA / "plant_stations.csv")
    df = summary.merge(stations, on="plant_id", suffixes=("", "_st"))
    print(f"Loaded {len(df)} plants with {df.shape[1]} columns")
    return df


def build_target(df):
    """Real outcome to predict: storm casualties plus nearby OSHA weather injuries.

    Training on an actual measured outcome (rather than a hand-made formula) lets the
    model learn which weather patterns predict harm, and generalizes to a new site
    where only the local climate is known.

    Components (all counts near the plant):
      storm_deaths           NOAA storm fatalities      weight 1.0
      storm_injuries         NOAA storm injuries        weight 0.3
      osha_weather_reports   OSHA weather-caused worker  weight 0.3
                             injuries (heat/cold/lightning)
    OSHA reports are each one injured worker, so they carry the same weight as a
    storm injury. This makes the hazard score reflect crew injuries, not just the
    public casualties NOAA records.
    """
    df["y"] = (
        df["storm_deaths"].fillna(0)
        + 0.3 * df["storm_injuries"].fillna(0)
        + 0.3 * df["osha_weather_reports"].fillna(0)
    )
    print("Target y summary:")
    print(df["y"].describe().round(2).to_string())
    return df


def build_matrix(df):
    """Assemble the numeric feature matrix X (climate cols + one-hot categoricals)."""
    X = df[CLIMATE_FEATURES].apply(pd.to_numeric, errors="coerce")
    dummies = pd.get_dummies(df[CATEGORICAL_FEATURES], dummy_na=False)
    X = pd.concat([X, dummies], axis=1)
    return X


def to_hazard_index(raw_predictions):
    """Map raw predictions to a 0-10 index.

    Casualty outcomes are heavily skewed, so we rank plants by prediction and scale
    the percentile rank to 0-10. This gives a readable spread instead of most plants
    bunched near zero. (Swap for MinMaxScaler if you prefer raw-magnitude scaling.)
    """
    ranks = pd.Series(raw_predictions).rank(pct=True)
    return (ranks * 10).round(1).to_numpy()


def main():
    df = load_features()
    df = build_target(df)

    X = build_matrix(df)
    y = df["y"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_STATE
    )

    model = XGBRegressor(
        n_estimators=300,
        max_depth=4,          # shallow: only ~694 rows, avoid overfitting
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=RANDOM_STATE,
    )
    model.fit(X_train, y_train)

    # --- Evaluate -----------------------------------------------------------
    pred_test = model.predict(X_test)
    print("\nHold-out performance:")
    print(f"  MAE: {mean_absolute_error(y_test, pred_test):.3f}")
    print(f"  R2 : {r2_score(y_test, pred_test):.3f}")

    # --- Score every plant and map to 0-10 ----------------------------------
    df["raw_prediction"] = model.predict(X)
    df["hazard_index"] = to_hazard_index(df["raw_prediction"])

    print("\nTop 10 highest-hazard plants:")
    top = df.sort_values("hazard_index", ascending=False)
    print(top[["plant_name", "state", "hazard_index"]].head(10).to_string(index=False))

    # --- Explainability: what drives the score ------------------------------
    importances = pd.Series(model.feature_importances_, index=X.columns)
    print("\nTop 10 feature importances:")
    print(importances.sort_values(ascending=False).head(10).round(4).to_string())

    # --- Save ----------------------------------------------------------------
    out = df[["plant_id", "plant_name", "state", "hazard_index"]].copy()
    out_path = DATA / "hazard_scores.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {len(out)} plant scores to {out_path}")


if __name__ == "__main__":
    main()
