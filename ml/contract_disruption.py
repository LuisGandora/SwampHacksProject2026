"""Did weather disrupt crew contracts, and did a bad revenue quarter follow?

    python scraper/contract_data.py          # first: USAspending contracts, SEC revenue, OpenFEMA
    python -m ml.contract_disruption         # load into Tiger, fit, attribute, write ml/models/contract_disruption.json

The team's hypothesis: when a crew contract fails and a bad revenue quarter follows, a severe weather event near the
work during the contract caused the disruption. A storm merely overlapping a failed contract can't show that (thunder-
storms touch most contracts that run a few months), so this tests it against contracts that had no such weather:

  1. Each contract is tied to the weather in its county during its ORIGINALLY PLANNED window (not the stretched one: a
     slipped contract runs longer and would collect more storms because of the delay, not before it). Weather = days
     with NOAA storm events by type, plus FEMA disaster declarations.
  2. Disrupted = terminated, or end date pushed 30+ days by changes other than planned option years.
  3. A logistic model gives each weather type's odds ratio for disruption, controlling for planned length, size, type of
     work, agency, state, start year and season. Weather types whose 95% interval sits above 1 are "disruptive".
  4. Per disrupted contract: probability weather caused it = 1 - P(disrupted | no disruptive weather) / P(disrupted |
     the weather it had), from step 3. This assumes the controls capture the other differences between exposed and
     unexposed contracts; it's an estimate, not proof.
  5. Evidence, per contract: a change document that says weather held the work up ("weather time extension",
     "Hurricane Ian impacts"), checked against NOAA for that kind of storm in the county while the contract ran:
       documented_and_corroborated > documented > storm_overlap_only (a storm happened; nothing ties it) > none.
     "Corroborated" needs the change to name a kind of weather (hurricane, flooding...) that NOAA recorded there; a
     plain "weather time extension" stays "documented". Attributed = disrupted and documented (either level), or step
     4's probability >= 0.5 ("more likely than not").
     A second model asks which weather types show up in documented weather delays (among disrupted contracts).
  6. Revenue: each public company's quarterly revenue growth (vs the same quarter a year earlier) against storm days in
     its territory, and whether stormy quarters are followed by bad quarters.

Public stand-ins: federal contracts are the same kind of field work as a utility's crew contracts, but not Sperry's own.
"""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
from psycopg.rows import dict_row
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

import psycopg
from db.setup_db import database_url

ROOT = Path(__file__).resolve().parent.parent
DATA, MODELS = ROOT / "data", ROOT / "ml" / "models"
MODEL_VERSION = "contract-disruption-v1"
SLIP_DAYS = 30
WEATHER_TYPES = {
    "tornado": ["Tornado"],
    "tropical": ["Tropical Storm", "Hurricane", "Hurricane (Typhoon)", "Tropical Depression", "Storm Surge/Tide"],
    "severe_wind": ["Thunderstorm Wind", "High Wind", "Strong Wind"],
    "hail": ["Hail"],
    "flood": ["Flash Flood", "Flood", "Heavy Rain", "Coastal Flood"],
    "lightning": ["Lightning"],
    "heat": ["Heat", "Excessive Heat"],
    "winter": ["Winter Weather", "Winter Storm", "Frost/Freeze", "Cold/Wind Chill", "Extreme Cold/Wind Chill", "Ice Storm"],
}
EXPOSURES = list(WEATHER_TYPES) + ["fema_declaration"]
FEMA_WEATHER = {"Hurricane", "Tropical Storm", "Severe Storm", "Severe Storm(s)", "Winter Storm", "Tornado",
                "Severe Ice Storm", "Flood", "Coastal Storm", "Snowstorm", "Typhoon"}
CONTRACTORS = {"QUANTA": "Quanta Services", "MASTEC": "MasTec", "MYR ": "MYR Group", "DYCOM": "Dycom Industries",
               "PRIMORIS": "Primoris Services", "EMCOR": "EMCOR Group", "GRANITE CONSTRUCTION": "Granite Construction",
               "STERLING": "Sterling Infrastructure"}
# Words in a contract change -> the NOAA weather types that would corroborate them (None: any type)
CITED_KINDS = [(r"\b(hurricane|tropical|named storm|storm surge)", {"tropical"}), (r"\btornado", {"tornado"}),
               (r"\b(flood\w*|rain|rainfall|wet season)\b", {"flood", "tropical"}), (r"\blightning\b", {"lightning"}),
               (r"\b(high )?winds?\b", {"severe_wind", "tropical", "tornado"}), (r"\b(freez\w*|cold|ice|icy)\b", {"winter"}),
               (r"\bheat\b", {"heat"})]
UTILITY_SITES = {"NextEra Energy": ["Florida Power & Light", "Gulf Power"], "Southern Company": ["Georgia Power", "Alabama Power"],
                 "Duke Energy": ["Duke Energy Florida"], "Tampa Electric": ["Tampa Electric"]}


# ------------------------------------------------------------------------------------------------------- load
def load(conn):
    c = pd.read_csv(DATA / "field_contracts.csv", dtype={"county_fips": str, "naics": str, "piid": str})
    c["county_fips"] = c.county_fips.str.zfill(5)
    copy(conn, "field_contracts", c)
    f = pd.read_csv(DATA / "fema_declarations.csv", dtype={"county_fips": str}).drop_duplicates(["disaster_number", "county_fips"])
    copy(conn, "fema_declarations", f)
    p = pd.read_csv(DATA / "fema_utility_projects.csv", dtype={"county_fips": str}).drop_duplicates(["disaster_number", "pw_number"])
    copy(conn, "fema_utility_projects", p)
    r = pd.read_csv(DATA / "company_quarterly_revenue.csv")
    copy(conn, "company_quarterly_revenue", r)
    return c, f, r


def copy(conn, table, df):
    conn.execute(f"TRUNCATE {table} CASCADE")
    rows = df.astype(object).where(pd.notna(df), None).to_numpy().tolist()  # plain Python values for COPY
    with conn.cursor().copy(f"COPY {table} ({', '.join(df.columns)}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)


def zone_counties():
    """{(state abbrev, zone number): [county fips]} from the cached NWS zone-county file (scraper/relate_weather.py)."""
    path = max((DATA / "raw").glob("bp*.dbx"), key=lambda p: p.stat().st_mtime)
    out = {}
    for line in path.read_text("latin-1").splitlines():
        f = line.split("|")
        if len(f) >= 7:
            out.setdefault((f[0], f[1]), []).append(f[6])
    return out


def storm_days(conn):
    """One row per (county fips, weather type, day) from NOAA Storm Events; zone events spread to the zone's counties."""
    ev = pd.DataFrame(conn.execute(
        "SELECT state, state_fips, cz_type, cz_fips, event_type, begin_time::date AS day FROM storm_events "
        "WHERE state IN ('Florida', 'Georgia', 'Alabama')").fetchall())
    kind = {t: k for k, types in WEATHER_TYPES.items() for t in types}
    ev["kind"] = ev.event_type.map(kind)
    ev = ev.dropna(subset=["kind"])
    abbrev = {"Florida": "FL", "Georgia": "GA", "Alabama": "AL"}
    zones = zone_counties()
    rows = []
    for e in ev.itertuples():
        if e.cz_type == "C":
            rows.append((f"{int(e.state_fips):02d}{int(e.cz_fips):03d}", e.kind, e.day))
        else:
            rows += [(fips, e.kind, e.day) for fips in zones.get((abbrev[e.state], f"{int(e.cz_fips):03d}"), [])]
    return pd.DataFrame(rows, columns=["county_fips", "kind", "day"]).drop_duplicates()


# ------------------------------------------------------------------------------------------------- exposure
def exposure(c, days, fema, data_end):
    """Weather days by type in each contract's county during its planned window."""
    days = days.assign(day=pd.to_datetime(days.day))
    by_county = {k: g for k, g in days.groupby("county_fips")}
    fema = fema[fema.incident_type.isin(FEMA_WEATHER)]  # not COVID ("Biological") or fire declarations
    fema = fema.assign(incident_begin=pd.to_datetime(fema.incident_begin), incident_end=pd.to_datetime(fema.incident_end))
    fema_by = {k: g for k, g in fema.groupby("county_fips")}
    out = []
    for r in c.itertuples():
        g = by_county.get(r.county_fips)
        row = dict.fromkeys(EXPOSURES, 0)
        if g is not None:
            inside = g[(g.day >= r.start) & (g.day <= min(r.planned_end, data_end))]
            row.update(inside.groupby("kind").size().to_dict())
        fg = fema_by.get(r.county_fips)
        if fg is not None:
            row["fema_declaration"] = int(((fg.incident_begin <= r.planned_end) & (fg.incident_end >= r.start)).sum())
        out.append(row)
    return pd.DataFrame(out, index=c.index)


def evidence(c, days):
    """Per contract: documented_and_corroborated / documented / storm_overlap_only / none, and the matching NOAA days."""
    import re
    days = days.assign(day=pd.to_datetime(days.day))
    by_county = {k: g for k, g in days.groupby("county_fips")}
    out, match = [], []
    for r in c.itertuples():
        g = by_county.get(r.county_fips)
        final = pd.to_datetime(r.final_end_date, errors="coerce")
        end = r.planned_end if pd.isna(final) else max(final, r.planned_end)  # documented delays happen in the stretch
        ran = g[(g.day >= r.start) & (g.day <= end)] if g is not None else None
        if not r.weather_cited:
            out.append("storm_overlap_only" if r.disrupted and ran is not None and len(ran) else "none")
            match.append(None)
            continue
        text = str(r.weather_cited_text).lower()
        kinds = set().union(*[k for pat, k in CITED_KINDS if re.search(pat, text)])
        # only a NAMED kind of weather can be corroborated: some storm hits almost any long contract's county
        hit = ran[ran.kind.isin(kinds)] if kinds and ran is not None else None
        ok = hit is not None and len(hit) > 0
        out.append("documented_and_corroborated" if ok else "documented")
        match.append(", ".join(f"{k} {d:%Y-%m-%d}" for k, d in hit.sort_values("day")[["kind", "day"]].head(3).itertuples(index=False))
                     if ok else None)
    return out, match


def design(c, x):
    """Model matrix: one exposed/not column per weather type, then the controls."""
    weather = (x[EXPOSURES] > 0).astype(float)
    weather.columns = [f"w_{k}" for k in EXPOSURES]
    top_agency = c.agency.value_counts().index[:6]
    controls = pd.concat([
        np.log(c.planned_days.clip(lower=1)).rename("log_planned_days"),
        np.log1p(c.potential_value_usd.clip(lower=0)).rename("log_value"),
        (c.start.dt.year - 2020).rename("start_year"),
        pd.get_dummies(c.naics.str[:4], prefix="naics", drop_first=True, dtype=float),
        pd.get_dummies(c.agency.where(c.agency.isin(top_agency), "Other"), prefix="agency", drop_first=True, dtype=float),
        pd.get_dummies(c.state, prefix="state", drop_first=True, dtype=float),
        pd.get_dummies(c.start.dt.quarter, prefix="start_q", drop_first=True, dtype=float),
    ], axis=1)
    return pd.concat([weather, controls], axis=1)


def odds_ratios(model, X):
    """Odds ratio and 95% interval per column, from the inverse Fisher information (plain MLE standard errors)."""
    A = np.column_stack([np.ones(len(X)), X.to_numpy(float)])
    p = model.predict_proba(X)[:, 1]
    cov = np.linalg.pinv(A.T @ (A * (p * (1 - p))[:, None]))
    beta = np.r_[model.intercept_, model.coef_[0]]
    se = np.sqrt(np.diag(cov))
    z = beta / se
    return pd.DataFrame({"coef": beta, "se": se, "odds_ratio": np.exp(beta), "ci_low": np.exp(beta - 1.96 * se),
                         "ci_high": np.exp(beta + 1.96 * se), "p_value": 2 * stats.norm.sf(np.abs(z))},
                        index=["intercept", *X.columns]).iloc[1:]


# ---------------------------------------------------------------------------------------------------- revenue
def revenue_tests(conn, rev, days, contracts):
    """Per company: storm days in its territory each quarter vs revenue growth (same quarter a year earlier)."""
    rev = rev.assign(quarter_end=pd.to_datetime(rev.quarter_end)).sort_values(["cik", "quarter_end"])
    rev["q"] = rev.quarter_end.dt.to_period("Q")
    rev = rev.drop_duplicates(["cik", "q"], keep="last")
    prev = rev.assign(q=rev.q + 4)[["cik", "q", "revenue_usd"]].rename(columns={"revenue_usd": "rev_prev"})
    rev = rev.merge(prev, on=["cik", "q"], how="left")
    rev["yoy"] = np.log(rev.revenue_usd / rev.rev_prev)
    major = days[days.kind.isin(["tropical", "tornado"])].assign(q=lambda d: pd.to_datetime(d.day).dt.to_period("Q"))
    site_counties = pd.DataFrame(conn.execute("SELECT county_fips, utility_name FROM sites WHERE county_fips IS NOT NULL").fetchall())
    out = []
    for (cik, company, kind), g in rev.dropna(subset=["yoy"]).groupby(["cik", "company", "kind"]):
        if kind == "utility":
            names = UTILITY_SITES.get(company, [])
            counties = set(site_counties[site_counties.utility_name.fillna("").str.contains("|".join(names), regex=True)].county_fips) if names else set()
            m = major[major.county_fips.isin(counties)]
        else:
            m = major  # contractors: storms anywhere in FL/GA/AL (their restoration work follows storms)
        storms = m.drop_duplicates(["county_fips", "day"]).groupby("q").size()  # county-days with a major storm
        g = g.assign(storm_county_days=g.q.map(storms).fillna(0))
        g["storm_next"] = g.storm_county_days.shift(1)  # the previous quarter's storms, for this quarter's growth
        bad = g.yoy <= g.yoy.quantile(0.25)
        stormy = g.storm_county_days >= g.storm_county_days.quantile(0.75)
        r_same = stats.spearmanr(g.storm_county_days, g.yoy) if len(g) >= 12 else (np.nan, np.nan)
        lag = g.dropna(subset=["storm_next"])
        r_next = stats.spearmanr(lag.storm_next, lag.yoy) if len(lag) >= 12 else (np.nan, np.nan)
        prev_stormy = stormy.shift(1, fill_value=False)
        out.append({"company": company, "kind": kind, "quarters": len(g),
                    "spearman_same_quarter": round(float(r_same[0]), 3), "p_same_quarter": round(float(r_same[1]), 4),
                    "spearman_next_quarter": round(float(r_next[0]), 3), "p_next_quarter": round(float(r_next[1]), 4),
                    "bad_quarter_rate_after_stormy": round(float(bad[prev_stormy].mean()), 3) if prev_stormy.any() else None,
                    "bad_quarter_rate_after_calm": round(float(bad[~prev_stormy].mean()), 3),
                    "contracts_held": int(contracts.recipient_parent.fillna("").str.upper().str.contains(
                        next((k for k, v in CONTRACTORS.items() if v == company), "\0"), regex=False).sum())
                    if kind == "contractor" else None})
    return out


# ------------------------------------------------------------------------------------------------------- main
def main():
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        c, fema, rev = load(conn)
        days = storm_days(conn)
        data_end = pd.Timestamp(conn.execute("SELECT max(begin_time)::date AS d FROM storm_events").fetchone()["d"])

        c["start"] = pd.to_datetime(c.start_date, errors="coerce")
        c["planned_end"] = pd.to_datetime(c.original_end_date, errors="coerce")
        c["planned_days"] = (c.planned_end - c.start).dt.days
        # fully observed: planned window inside the weather record, and at least 90 days since it ended to see slips
        keep = c.start.notna() & (c.planned_days > 0) & (c.start >= "2015-01-01") & \
               (c.planned_end <= data_end - pd.Timedelta(days=90))
        c = c[keep].reset_index(drop=True)
        c["disrupted"] = (c.terminated.fillna("") != "") | (c.slip_days >= SLIP_DAYS)
        x = exposure(c, days, fema, data_end)
        X = design(c, x)
        y = c.disrupted.astype(int).to_numpy()

        # how much weather adds: cross-validated AUC with and without the weather columns
        cv = StratifiedKFold(5, shuffle=True, random_state=0)
        fit = lambda: LogisticRegression(C=1e4, max_iter=5000)  # effectively unpenalized: plain odds ratios
        controls = [col for col in X.columns if not col.startswith("w_")]
        auc_full = roc_auc_score(y, cross_val_predict(fit(), X, y, cv=cv, method="predict_proba")[:, 1])
        auc_ctrl = roc_auc_score(y, cross_val_predict(fit(), X[controls], y, cv=cv, method="predict_proba")[:, 1])

        model = fit().fit(X, y)
        ors = odds_ratios(model, X)
        weather_or = ors.loc[[f"w_{k}" for k in EXPOSURES]]
        disruptive = [k for k in EXPOSURES if weather_or.loc[f"w_{k}", "ci_low"] > 1]

        p1 = model.predict_proba(X)[:, 1]
        X0 = X.copy()
        X0[[f"w_{k}" for k in disruptive]] = 0
        p0 = model.predict_proba(X0)[:, 1]
        pc = np.where(p1 > p0, 1 - p0 / p1, 0.0)
        c["p1"], c["p0"], c["pc"] = p1, p0, pc
        c["evidence"], c["storm_match"] = evidence(c, days)
        c["attributed"] = c.disrupted & (c.evidence.str.startswith("documented") | (c.pc >= 0.5))
        coef = weather_or.coef
        c["top_weather"] = [max((k for k in disruptive if X.at[i, f"w_{k}"]), key=lambda k: coef[f"w_{k}"], default=None)
                            for i in c.index]

        sites = pd.DataFrame(conn.execute("SELECT site_id, county_fips FROM sites WHERE county_fips IS NOT NULL").fetchall())
        site_ids = sites.groupby("county_fips").site_id.apply(list).to_dict()
        conn.execute("DELETE FROM contract_weather_attribution")
        conn.cursor().executemany(
            "INSERT INTO contract_weather_attribution (award_key, model_version, disrupted, exposure, p_disruption, "
            "p_without_weather, prob_weather_caused, attributed, top_weather, site_ids, evidence, storm_match) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(r.award_key, MODEL_VERSION, bool(r.disrupted), json.dumps({k: int(x.at[i, k]) for k in EXPOSURES}),
              round(float(r.p1), 4), round(float(r.p0), 4), round(float(r.pc), 4), bool(r.attributed),
              r.top_weather or (r.storm_match.split(" ")[0] if isinstance(r.storm_match, str) else None),
              site_ids.get(r.county_fips, []), r.evidence, r.storm_match if isinstance(r.storm_match, str) else None)
             for i, r in c.iterrows()])

        # which weather shows up in DOCUMENTED weather delays (among disrupted contracts)
        d = c[c.disrupted]
        keep_cols = [f"w_{k}" for k in EXPOSURES] + ["log_planned_days", "log_value", "start_year"] +                     [col for col in X.columns if col.startswith("state_")]
        Xd, yd = X.loc[d.index, keep_cols], d.weather_cited.astype(int).to_numpy()
        doc_model = fit().fit(Xd, yd)
        doc_or = odds_ratios(doc_model, Xd).loc[[f"w_{k}" for k in EXPOSURES]]
        doc_auc = roc_auc_score(yd, cross_val_predict(fit(), Xd, yd, cv=cv, method="predict_proba")[:, 1])
        # the team's original rule: "a tornado near a failed contract means the tornado caused it"
        tor = d[x.loc[d.index, "tornado"] > 0]
        rule = {"disrupted_with_tornado": len(tor),
                "of_those_documented_weather": round(float(tor.weather_cited.astype(bool).mean()), 3),
                "documented_weather_rate_all_disrupted": round(float(d.weather_cited.astype(bool).mean()), 3)}

        # check against the contracts' own words
        cited, uncited = d[d.weather_cited.astype(bool)], d[~d.weather_cited.astype(bool)]
        text_check = {
            "disrupted_contracts_citing_weather": len(cited),
            "attributed_rate_when_text_cites_weather": round(float(cited.attributed.mean()), 3) if len(cited) else None,
            "attributed_rate_otherwise": round(float(uncited.attributed.mean()), 3),
            "mean_prob_weather_caused_cited": round(float(cited.pc.mean()), 3) if len(cited) else None,
            "mean_prob_weather_caused_otherwise": round(float(uncited.pc.mean()), 3),
        }
        if len(cited) >= 5:
            text_check["auc_prob_weather_caused_vs_text"] = round(float(roc_auc_score(d.weather_cited.astype(bool), d.pc)), 3)

        revenue = revenue_tests(conn, rev, days, c)
        result = {
            "model_version": MODEL_VERSION, "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "contracts": len(c), "disrupted": int(c.disrupted.sum()),
            "terminated": int((c.terminated.fillna("") != "").sum()), "slipped_30_days": int((c.slip_days >= SLIP_DAYS).sum()),
            "attributed_to_weather": int(c.attributed.sum()),
            "attributed_by_weather": c[c.attributed].storm_match.fillna("unnamed weather").str.split(" ").str[0].value_counts().to_dict(),
            "evidence": c[c.disrupted].evidence.value_counts().to_dict(),
            "documented_weather_odds_ratios": {k: {m: round(float(doc_or.loc[f"w_{k}", m]), 4)
                                                   for m in ("odds_ratio", "ci_low", "ci_high", "p_value")} for k in EXPOSURES},
            "documented_weather_cv_auc": round(doc_auc, 3),
            "team_rule_check": rule,
            "exposed_share": {k: round(float((x[k] > 0).mean()), 3) for k in EXPOSURES},
            "cv_auc_with_weather": round(auc_full, 3), "cv_auc_controls_only": round(auc_ctrl, 3),
            "disruptive_weather": disruptive,
            "weather_odds_ratios": {k: {m: round(float(weather_or.loc[f"w_{k}", m]), 4)
                                        for m in ("odds_ratio", "ci_low", "ci_high", "p_value")} for k in EXPOSURES},
            "text_check": text_check,
            "revenue": revenue,
            "sources": ["USAspending.gov contract transactions (FL/GA/AL, NAICS 2371/2373/2379/238210/2389, FY2016-)",
                        "NOAA Storm Events", "OpenFEMA disaster declarations", "SEC EDGAR XBRL quarterly revenue"],
            "caveats": ["Federal contracts stand in for a utility's crew contracts: same kind of field work, not the "
                        "same owner.", "Attribution assumes the controls capture other differences between exposed "
                        "and unexposed contracts: an estimate, not proof.", "Correlation is not causation."],
        }
        conn.commit()
    (MODELS / "contract_disruption.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps({k: v for k, v in result.items() if k not in ("revenue", "sources", "caveats")}, indent=2, default=str))
    print("revenue:")
    for r in revenue:
        print("  ", r)


if __name__ == "__main__":
    main()
