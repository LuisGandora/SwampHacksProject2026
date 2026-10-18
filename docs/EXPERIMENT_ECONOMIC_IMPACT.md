# Experiment 3: what does weather cost the companies that run these plants?

**Hypothesis:** weather at the sites shows up in the companies' economics in three places:
1. more restoration work for crews,
2. different sales,
3. crew time lost to stop-work weather.

**Verdict:**

| Part | Verdict | Strength |
|---|---|---|
| Weather drives crew workload | **Supported** | r = 0.37–0.72 at every large grid operator, p < 10⁻⁶, seasons removed |
| Weather moves real sales | **Supported** | Degree days lift the fit of monthly sales from R² 0.06–0.23 to 0.85–0.98 at six utilities |
| Weather costs crew time | **Estimated, not measured** | About $18.7M over five years across the 694 sites, from labeled assumptions; real measurement has started |

Experiments 1 and 2 (contract disruption, restoration bounceback) are in [EXPERIMENTS.md](EXPERIMENTS.md). Both were *not supported*; their findings are summarized here only where the app shows them.

## Data

| What | Source | Real or stand-in | Size |
|---|---|---|---|
| Who runs each plant | EIA-860M plant → utility and grid operator (`scraper/economics.py`) | Real | 694 plants |
| Monthly retail sales and revenue | EIA-861M | **Real** | 8 utilities, Jan 2021 – Jul 2026 |
| Daily grid demand | EIA-930, per grid operator | Real | 8,454 operator-days |
| Weekly revenue | demand × that month's average EIA-861M price | Stand-in (estimate) | – |
| Restoration workload | EAGLE-I (Oak Ridge National Lab) customer-hours without power, in the counties holding each operator's sites | Stand-in for crew trips | 2021–2025 |
| **Crew dispatches** | **Utilities' public outage maps** (new, see below) | **Real**, from 17 Oct 2026 on | grows every 15 min |
| Labor cost | BLS wages × benefits for power-line installers: FL $58.33, GA $56.26, AL $56.65 per hour | Real public rate | – |
| Weather at each site | IEM daily station summaries (`weather_daily`) | Real | Oct 2021 – Oct 2026 |
| Hours lost per hazard, crew size, fix costs | `economic_assumptions` | **Assumptions**, editable in the app | – |

## Method

- **Workload and demand** (`ml/economic_impact.py`):
  - Weekly, per grid operator: stop-work site-days, storm events, demand, outage customer-hours and estimated revenue.
  - Each pair is correlated raw and **deseasonalized**: each series minus its same-month average. Summer heat and summer demand rise together anyway, so the useful question is whether an *unusual* week of weather comes with *unusual* workload or demand.
- **Sales** (`ml/weather_sales.py`, new): the degree-day model utilities themselves use for the weather line in their SEC filings.
  - Per utility and state: `monthly sales = a + b·trend + c·days in month + d·CDD + e·HDD`.
  - CDD and HDD (cooling and heating degree days, base 65 °F) come from the stations at that utility's own sites.
  - The weather part of a month is `d·(CDD − normal) + e·(HDD − normal)`, where "normal" is that calendar month's average. It's priced at that month's real average retail price.
  - The check: R² with and without the weather terms.
- **Crew time lost:** stop-work site-days × hours lost per hazard (capped at one shift) × crew size × the state's labor rate.

## Results

### 1. Weather drives crew workload (supported)

Weeks with more stop-work weather at an operator's sites have more customer-hours without power in those counties. Deseasonalized, 143–220 weeks each:

| Grid operator | Stop-work weather vs outages | Storm events vs outages |
|---|---|---|
| Tampa Electric (TEC) | **0.71** | 0.59 |
| Florida Municipal Power Pool (FMPP) | 0.70 | 0.65 |
| Duke Energy Florida (FPC) | 0.69 | 0.55 |
| TVA | 0.52 | 0.57 |
| Florida Power & Light (FPL) | 0.51 | 0.38 |
| Seminole Electric (SEC) | 0.50 | **0.72** |
| Southern Company (SOCO) | 0.50 | – |

All p < 10⁻⁶. In total, 11 operators are significant at p < 0.001 for stop-work weather vs outages.

### 2. Weather moves sales (supported)

**Demand:** unusually hot weeks raise demand at all four large operators: FPL r = 0.38, Duke Florida 0.36, Southern 0.36, Tampa 0.33 (p < 10⁻⁶). Tampa Electric's stormy weeks have *lower* demand (r = −0.28), consistent with customers losing power.

**Real monthly sales, degree-day model:**

| Utility | Fit without → with weather | MWh per cooling degree-day | Weather in sales by year ($M) |
|---|---|---|---|
| Alabama Power | 0.13 → **0.98** | 3,548 | 2022 +16 · **2023 −70** · 2024 +63 · 2025 +37 |
| Florida Power & Light | 0.17 → 0.98 | 8,611 | 2022 −65 · 2023 +56 · 2024 +102 · 2025 −112 |
| Georgia Power | 0.20 → 0.97 | 5,607 | 2022 +13 · **2023 −147** · 2024 +100 · 2025 +79 |
| JEA | 0.23 → 0.98 | 910 | 2022 −3 · 2023 −12 · 2024 +14 · 2025 −2 |
| Duke Energy Florida | 0.07 → 0.89 | 3,232 | 2022 −33 · 2023 −42 · 2024 +64 · 2025 −40 |
| Tampa Electric | 0.06 → 0.85 | 1,508 | 2022 −2 · 2023 −16 · 2024 +5 · 2025 −27 |
| TVA (Alabama only) | 0.63 → 0.68 | 66 | about ±1 (our few Alabama sites can't represent TVA's whole territory) |

**Cross-check:** the model's large 2023 losses for Georgia Power and Alabama Power match what Southern Company told investors. It said 2023 had "extremely mild weather" in the Southeast, and that the first quarter was the mildest on record. The model can be tied out exactly against the "weather" line in each utility's 10-Q retail revenue table on SEC EDGAR; that comparison hasn't been done yet.

**Monthly sales vs our site weather** (simple correlation, n = 58): hot months raise sales at Georgia Power, Alabama Power and FPL (r ≈ 0.29–0.31, p < 0.05).

### 3. Crew time lost (estimate)

- **Over five years:** 324,116 crew-hours, about **$18.7M**, across all operators. This was recomputed after the wind fix below; it was about $28M before.
- **Per site:** about $3.7M a year in total. The worst sites lose about $12k a year each (Plan → Invest).
- **What these numbers are:** assumption-driven estimates. Every unmeasured input is a labeled row, and supervisors can now replace them in the app.

## What changed while running it

- **Sensor errors fixed at the source.** Two stations, EUF (Eufaula, AL) and BCR, logged "sustained wind" of 200–959 kt for days, with no gust. Those 235 readings counted as high-wind stop-work days and pushed Georgia and Alabama sites to the top of the Invest list. Readings over 87 kt sustained without a gust at least as high are now blanked: in the database, on history load (`db/load_history.py`) and on the daily update (`backend/daily_weather.py`). After the fix, the top sites are Florida sites with soil saturation and high wind, which is plausible.
- **Real crew data started.** See below.

## Getting real crew trip logs

No public dataset lists utility crew trips. Options, best first:

| Option | What it gives | Coverage | Status |
|---|---|---|---|
| **Utilities' public outage maps** (KUBRA StormCenter) | Each outage with the utility's own status text: "Personnel have been assigned…", "Personnel are currently on site repairing… The cause of the outage is a dead tree." Polled every 15 minutes, that gives assigned / on site / restored times and the cause. | Many US utilities use KUBRA. Configured now: Alabama Power, Georgia Power, Mississippi Power, JEA. Each is filtered to outages within 25 mi of our sites. | **Built:** `backend/crew_dispatch.py`, worker job `crew_dispatch`. Finished outages with a crew become `crew_trips` rows (`source = 'outage_map'`). |
| Sperry or a utility's own work orders | Real trips, crew size, hours, cost | Their territory | Loader ready: `data/crew_trips.csv` → `crew_trips` (`source = 'company'`) |
| DOE **ODIN** (Outage Data Initiative Nationwide, ORNL) | Standardized outage events from participating utilities. The public API is county-level; finer detail needs a DOE-approved account. | Growing national list (`odin.ornl.gov/odi/status`) | Not built. Request access if the team wants a second national feed. |
| Florida PSC annual Distribution Reliability Reports | Outage events per utility by cause (each needs a trouble crew), plus excluded storm events with "restoration time and resources" | FL investor-owned utilities, yearly | Not built. Good for a yearly sanity check. |
| EAGLE-I customer-hours out | Workload, not trips | National, county, 15-min | In use as the stand-in |

**First pass:** 35 outages near our sites. Alabama Power had marked a crew assigned on 10 of 13 (5 on site; one cause: a dead tree). Georgia Power's map showed no crew status that night. Duke Energy and FPL run their own maps, so they need their own collector.

**Limits:**
- The endpoints are public but undocumented. A changed map is skipped and reported, never fatal.
- Polling is capped at 400 tile requests per map per run.
- History starts when the job first ran (17 Oct 2026); there's no backfill.
- Outage maps don't give crew size.
- Check each utility's terms of use before relying on its map commercially.

## Making hours lost and crew size real

- **Measured stop-work time:** the app now measures how long its own stop-work alerts actually last per site-day (cleared, or until the all-clear passed). The figure appears next to each assumed "hours lost" value. First readings: lightning 0.5 h per site-day (11 site-days), high wind 1.0 h (19 site-days), against assumed values of 2 h each. A few more weeks of alerts will tell whether the assumptions are too high.
- **Editable assumptions:** Plan → Economics → *Assumptions*. An approved supervisor can type their company's crew size, shift length, labor rate, hours lost per hazard, fix costs and fix effectiveness.
  - The row is then marked `COMPANY: set by <who> on <date>`, and the BLS loader never overwrites it.
  - Invest, Economics, the week's at-risk dollars and the briefings use the new number immediately.
  - API: `PUT /economics/assumptions/{key}`, supervisors only, with range checks.

## Where it shows in the app

| Place | What |
|---|---|
| 📋 Plan → **Economics** (new) | Region totals: crew-hours and dollars at risk this week from the 7-day forecast; yearly weather loss; real outages and crew trips logged. Then the findings as plain sentences with their numbers, losses by utility, weather in real sales per utility, real crew dispatches, and the assumptions panel. |
| 📋 Plan → This week | At-risk crew-hours and dollars per site, per day, and in total |
| 📋 Plan → Invest | Yearly loss per site, fix, cost, payback |
| 🌙 Briefing | Each area: "≈ N crew-hours ($X) at risk tomorrow (estimate)". Gemini is told to include it. |
| Site card → Economic impact | Weekly table and the operator's correlations |

## Reproduce

```powershell
python scraper/economics.py; python scraper/outages.py; python scraper/labor_costs.py   # data (once)
python -m ml.economic_impact      # weekly impact + correlations
python -m ml.weather_sales        # weather in real sales (degree-day model)
python -m backend.worker crew_dispatch   # one pass over the outage maps (the worker runs it every 15 min)
```

## Limits and what would change the verdicts

- **Correlation is not causation.** The outage stand-in counts all restoration work in a county, not one company's crews.
- **Sales:** the degree-day model uses only our sites' stations. For utilities with few sites (TVA), it doesn't represent the territory.
- **Crew-time dollars:** these stay estimates until hours lost and crew size come from work orders, or from enough measured alerts and dispatches.
- **What would make all three definitive:** a utility's own trip logs, crew sizes and weekly sales, loaded through the existing loaders.
