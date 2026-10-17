# PlanIT Control: brief → feature checklist

Every requirement in the Sperry Tech brief, mapped to what implements it, how to show it, and how we check it.
Status: ✅ done and verified (backend + UI) · 🟡 needs configuration · ❌ not started.

## Required

| # | Brief requirement | Feature | Endpoint / job | Demo step | Check | Status |
|---|---|---|---|---|---|---|
| 1 | At least 50 real power plant locations | 694 plants in FL, GA and AL from EIA-860M, cross-checked against OpenStreetMap. Sites more than 5 km off are corrected or flagged (14 corrected, 2 flagged unresolved). | `GET /sites` | Globe shows all 694 sites, colored by band | `coord_check` column; answer key: top-10 coordinates verified | ✅ (globe map) |
| 2 | One combined risk picture per site: weather, extreme heat, lightning, flooding, wildfire, soil saturation, wildlife | Live feeds (NWS forecasts, alerts and station readings, GOES-19 lightning, NOAA river gauges, NASA FIRMS, iNaturalist) plus 11 years of history per category radius | `GET /sites/{id}`, `/forecast`, `/hazard-events` | Tap a site: site card with scores, drivers, alerts, forecast chart, history | Live jobs return data (worker log) | ✅ |
| 3 | Instant alert: lightning within 10 mi → stop work, 30-min all-clear that restarts with each strike | `lightning` job every minute; `raise_site_alert()` restarts the countdown | `GET /alerts/active` (`all_clear_seconds`) | `POST /dev/lightning/{id}` twice: the countdown resets | `db/setup_db.py` countdown check; `test_backend` | ✅ |
| 4 | Instant alert: flood-risk warning for river sites after a night of heavy rain | 2 in or more in the last 12 h at the site's station → warning, naming the nearby river gauge. NOAA gauges at action stage or worse within 5 mi → warning or stop work. | `observations`, `rivers` jobs | Live: 13 flood warnings on first run | Alert messages cite gauge and distance | ✅ |
| 5 | Next-day safety briefing every evening at 6 PM local time | `briefings` job every 15 min, per site time zone. Optional Discord/Slack delivery. | `GET /briefings/{id}`; `notify` job | `POST /dev/briefings/{id}` | `daily_briefings.send_at` = 18:00 local | ✅ · 🟡 set `NOTIFY_WEBHOOK_URL` |
| 6 | Crew hazard reporting: photo, pin, share instantly, build a hazard database | Photo upload, report with location; nearest site gets an alert immediately (12 h). Stored for future project owners. Seeded with 13k OSHA reports. | `POST /uploads`, `POST /crew-reports`, `GET /crew-reports` | Report a spill with a photo: the alert appears | API test in round 2 | ✅ |
| 7 | Supervisor dashboard: map ranking sites by risk | Ranking by latest hazard score, then active stop-work and warning alerts | `GET /sites?sort` (default) | Ranking panel: Today/Tomorrow, band tiles, state filter, search | Answer key: top results sane | ✅ |
| 8 | Hazard index 0–10 from an ML model trained on historical weather and environmental data tied to hazards workers face | Site-day XGBoost per category (NOAA events + OSHA weather injuries as labels). Score = 10 × P(any hazard). Per-category drivers. Scores today and tomorrow. | `ml/hazard_model.py`; `scores` job | Score breakdown on a site | `python -m ml.answer_key` (11 checks) | ✅ |

## Beyond the brief (only after the core works)

| Feature | Why it follows from the core | Where |
|---|---|---|
| On-tap AI safety report | Explains the score and alerts. Code computes every number and the go/caution/no-go call. Sources cited, assumptions labeled, numbers audited. Template fallback. | `POST /sites/{id}/report`, `backend/reports.py` |
| Economic impact | Weather → lost crew time (assumption-based) → estimated cost, plus correlations of weather with real EIA utility demand and sales | `ml/economic_impact.py`, `GET /sites/{id}/economics`, `GET /economics/correlations` |
| Demo scenario | Shows the brief's opening story on any site, whatever the real weather | `POST /dev/scenario/{id}` |

## Helpful resources from the brief

| Resource | Used? |
|---|---|
| EIA-860M | ✅ site list |
| NOAA Storm Events | ✅ history and model labels |
| Iowa Environmental Mesonet | ✅ daily station weather (model features). NWS warning archive: not used. |
| OSHA Severe Injury Reports | ✅ crew-hazard seed data and heat/cold/lightning labels |
| National Levee Database | ❌ not used |
| NOAA Tides & Currents | 🟡 coastal gauges come in through NOAA NWPS; Tides & Currents API not used |
| FEMA Flood Map Service Center | ❌ not used (candidate: flood-zone flag per site) |

## Known limits (say these out loud at the table)

- Lightning comes from GOES-19 GLM, accurate to about 5 mi.
- Crew trips: no public source exists. Lost crew time is estimated from weather stop-work days × labeled assumptions until a utility's trip logs are loaded (`crew_trips`).
- Sales are monthly (EIA-861M). Weekly comparisons use grid demand (EIA-930) as the sales-volume measure.
- Tropical scores can't be tested out-of-sample: no tropical events hit these states in 2025.
