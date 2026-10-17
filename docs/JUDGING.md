# Judging kit

What to show, what to say, and what to check before judges see Planet Control. The brief is about **crew safety**, so the demo stays on safety. Economics gets one slide at most, or waits for a judge to ask.

## Slide 1: does the Hazard Index beat a simple baseline?

Walk-forward backtest ([ml/backtest.py](../ml/backtest.py) → [ml/models/backtest.json](../ml/models/backtest.json), also shown in the app under 📈 Model). Each test year is scored by a model trained only on earlier years, against what actually happened: NOAA storm events, deaths, injuries, damage of $1M or more, and OSHA weather injuries.

| Average over test years 2023, 2024, 2025–26 | Ranks hazard days (AUC) | Ranks severe-outcome days | Ranks death days | Severe days in the top 10% of scores | Calibration (1.0 = exact) |
|---|---|---|---|---|---|
| **Baseline:** this site's historical hazard rate for the calendar month (no weather) | 0.659 | 0.693 | 0.646 | 34% | 1.10 |
| **Planet Control v3** (day-ahead forecast + NWS alerts + severity) | **0.870** | **0.840** | **0.919** | **46%** | 0.92 |

**One line to say:** "Knowing the site and the month gets you 0.66. Adding tomorrow's forecast and the NWS alerts gets you 0.87, and death days go from 0.65 to 0.92."

Notes for questions:
- **AUC:** the chance that a real hazard day scores above a calm one. 0.5 is a coin flip, 1.0 is perfect.
- **Per-category test AUC, 2025–26:**
  - flooding 0.94
  - extreme heat 0.92
  - lightning 0.91
  - severe storm 0.90
  - high wind 0.85
  - extreme cold 0.98
- **Where the baseline wins:** in the 2025–26 test year it put more severe days in its top 10% (48% vs 33%). The model wins every other metric in every year. Say it before a judge finds it.
- **Live floors:** today's score can't fall below what's happening now. An active stop-work alert sets a floor of 8, a warning 5, an open crew report 4.

> **Team decision before judging:** the dashboard's "Tomorrow" ranking currently shows the **climatology index** (`climatology-csv-v1`), because it was loaded with a later timestamp than the v3 scores. That index is a fixed score per site, so it ranks sites, not days. On the same test it scores **0.533** (hazard days), 0.640 (severe) and 0.534 (death days): below the site × month baseline. It's good as a long-term "how exposed is this plant" figure, but the daily Today/Tomorrow ranking, the alerts and slide 1 should use v3. Either let the hourly `scores` job overwrite it, or show the climatology index as a separate "long-term exposure" number.

## Slide 2 (optional): economics, one slide
"We also tested two economic hypotheses with public data: weather causing failed crew contracts, and a restoration bounceback after storms. Neither held up once compared fairly against controls. For example, a tornado near a failed contract explains it only 4.6% of the time, and an apparent +31% hail 'bounce' was the September fiscal-year spike. Details are in [docs/EXPERIMENTS.md](EXPERIMENTS.md)." Show it only if asked. Don't put it in the demo.

## Demo script (90 seconds, safety only)
Run it from the **demo instance** ([DEPLOY.md](DEPLOY.md)), signed in as a supervisor.
1. **Globe and ranking:** "694 real plants in FL, GA and AL, ranked by tomorrow's Hazard Index." Point out the 🌊 FLOODPLAIN badge: 106 sites are in FEMA's 100-year floodplain.
2. **▶ Demo:** the brief's day plays out. Heat index 108°F, soft ground after a week of rain, a crew member reports an alligator with a photo.
3. **Lightning 4 mi away:** the full-width STOP WORK screen and its 30-minute all-clear. A second strike resets it to 30:00.
4. **Phone:** report a hazard with the camera. With no signal it queues, then sends when back online.
5. **Supervisor:** the ranking puts the site on top (live floor 8). The alert arrives in the team's Discord or Slack. The 🛰 AI safety report gives GO / CAUTION / NO-GO with sources cited.
6. **6 PM briefing:** tomorrow's briefing for the site.
7. **■ Reset.**

## Questions to prepare for

**Why only FL, GA and AL when the brief mentions the Mississippi?**
The brief's opening scene is an Everglades jobsite, and FL, GA and AL together have 694 plants: far past the 50 required, with hurricane, swamp, heat and lightning exposure. Everything is keyed by state and NWS zone, not hard-coded. Adding Mississippi or Louisiana is a scraper argument plus a re-run of the history load. The backtest framework and alert rules don't change.

**Why doesn't the model use wildfire, soil saturation or wildlife?**
The model scores hazards with long, well-labeled records (NOAA storm events and OSHA injuries). The other three are covered by **live rules**, which fire instantly and show on the same site card. Each rule is a row in `hazard_categories`:

| Hazard | Rule | Source |
|---|---|---|
| Wildfire | Satellite fire detection within 10 mi → warning, within 2 mi → stop work. NWS Red Flag Warning → warning. | NASA FIRMS (needs `FIRMS_MAP_KEY`), NWS alerts |
| Soil saturation | 4 in of rain in 7 days → warning, 8 in → stop work | Station rain (`backend/observations.py`) |
| Wildlife | Sighting within 1 mi in the last 7 days → warning, with the photo | Crew reports and iNaturalist (alligators, snakes) |
| Flooding (in addition to the model) | 2 in of rain overnight → warning, 4 in → stop work; FEMA floodplain sites warned at 75% of that. River gauge at flood stage nearby. NWS flood warnings. | Stations, NOAA river gauges, FEMA NFHL, NWS |

**Doesn't training on observed weather and scoring tomorrow from forecasts skew the model?**
It does, which is why v3 trains on what was actually forecast: the NBM forecast issued by 6 PM the day before, for 2021–2026, with NWS alerts counted only if issued by then. The backtest measures the skew directly:
- **v2, trained on measured weather:** 0.904 when given measured weather, but 0.864 when given the forecast, and its probabilities came out at half of reality (calibration 0.54).
- **v3, trained on forecasts:** 0.884, with calibration 1.06.

The remaining gap is that training uses NBM forecasts while live scoring uses NWS gridpoint forecasts. The answer key flags one drift: NBM gust forecasts dropped in 2025 (known issue).

**Others likely to come up:**
- **Can Gemini change a decision?** No. Code computes every number and the go/no-go call. Gemini only explains them, citing source IDs, and a number audit rejects any figure not in the facts.
- **Is it secure?**
  - Auth0 sign-in, with RS256 tokens verified against the tenant's keys.
  - A users table, and per-user Discord/Slack webhooks stored encrypted.
  - Rate limits on writes.
  - A least-privilege database user for the API.
  - Demo endpoints are supervisor-only and off on the public URL.

## Hygiene checklist (check before judges see the repo or link)

| Check | Status | How to check |
|---|---|---|
| `.env.api` and `frontend/.env` never committed | ✅ | `git log --all --oneline -- .env.api frontend/.env` prints nothing |
| `.env` in history | ⚠️ in commit `a2ecf3b`, and still in the current files on `main`, `hal-larp-jordan` and `hal-larp-jordan(DONT_PULL)`. **The password in it is dead**: rotated, and the database rejects it. Before making the repo public, delete it from those branches. Consider rewriting history (`git filter-repo --path .env --invert-paths`, then force-push, and every teammate re-clones), or publish a fresh repo without the history. | `git log --all --oneline -- .env` |
| CDC county death counts under 10 hidden | ✅ CDC WONDER exports "Suppressed" for 1–9. The API and the AI report also mask 1–9 as "<10" (`reports.cdc_safe`, tested in `test_backend`). | `python -m backend.test_backend` |
| OSHA pins can't be mistaken for crew reports | ✅ hollow rings labeled "Historical OSHA injury report: public OSHA record, not a live crew report". Crew reports are filled dots. | 📍 Pins on the map |
| `PLANIT_DEV` off on the public URL | ✅ in `render.yaml` (only the demo API sets it) | `/health` returns `"dev": false` |
| Worker running (live alerts, scores, 6 PM briefings) | ⚠️ on judging day: exactly one `python -m backend.worker` | the most recent `weather_observations` row is minutes old |
| Hourly v3 scores newer than the climatology import | ⚠️ team decision above | `SELECT model_version FROM hazard_scores ORDER BY scored_for DESC LIMIT 1;` returns `site-day-xgb-v3` |
