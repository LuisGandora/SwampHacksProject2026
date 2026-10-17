# Experiments: weather and the economics of field work

Each experiment records the hypothesis, the data, the method, what went wrong along the way, the result and what would change it. All numbers come from the saved outputs. Re-run them with the commands in each section.

| # | Hypothesis | Verdict | Output |
|---|---|---|---|
| 1 | A severe weather event (such as a tornado) near a failed crew contract caused the failure, and a bad revenue quarter follows | **Not supported as stated.** A storm overlapping a failure is not evidence. 91 failures are attributable from the contracts' own records. | [ml/models/contract_disruption.json](../ml/models/contract_disruption.json) |
| 2 | After a weather impact, business bounces back above normal because of restoration work | **Not supported.** There is no bounce in any of the 15 tests. One apparent hail effect was a fiscal-year artifact. | [ml/models/bounceback.json](../ml/models/bounceback.json) |

## Data (shared by both experiments)

All of this data is public and needs no API keys. It stands in for company records until a utility shares its own.

```powershell
python scraper/contract_data.py      # USAspending, SEC, OpenFEMA (state and national); ~2 minutes
```

| Source | What it gives | Size |
|---|---|---|
| **USAspending.gov** | Federal field-construction contracts performed in FL/GA/AL since FY2016, with every change to them. Industry codes: power/utility lines 2371, highway and heavy civil 2373/2379, electrical 238210, site work 2389. Each change records the county where the work happened, its action type (terminate for default/cause/convenience, change order, option), the contract's end date after the change, and a free-text description. | 25,989 transactions → 7,647 contracts with their full history |
| **SEC EDGAR XBRL** | Quarterly revenue for 8 public line-crew and field contractors (Quanta, MasTec, MYR, Dycom, Primoris, EMCOR, Granite, Sterling) and 4 utility parents (NextEra, Southern, Duke, Tampa Electric). Q4 = fiscal year minus Q1–Q3. | 34–49 quarters each |
| **OpenFEMA** | Disaster declarations by county (FL/GA/AL; one row per disaster and county); utility-repair projects from FEMA's Public Assistance program, damage category F; major-disaster declarations nationwide | 3,430 · 5,407 · 16,119 |
| **NOAA Storm Events** (the yearly `StormEvents_details` files in `data/raw`) | Storm events by county, read straight from the files. Events recorded by forecast zone, such as hurricanes, heat and winter storms, are spread to that zone's counties using the NWS zone-county file. Zones NWS has since renumbered are matched by the county in the zone's name ("Coastal Lee" → Lee County). | 42,190 relevant FL/GA/AL events, 2015 to June 2026 → 24,605 county-days |
| **EIA-861M** (already in Tiger) | Monthly retail revenue for the sites' utilities | 2021–2026 |

**Data fixes made along the way.** Each one changed results, so each is checked by a test:
- **Storm data came from the database, not the data files.** The first runs read the `storm_events` table in Tiger. Its loader keeps only events it can put on a map, which drops 480 events these county-level tests need:
  - 361 zone events whose zone numbers NWS has since retired (125 tropical storm, 12 hurricane, 26 storm surge and more)
  - 119 county winter storms without coordinates

  Both experiments now read the NOAA files in `data/raw` directly and were re-run. Tropical county-days went from 1,392 to 1,481, and winter from 1,894 to 1,973. Days are now each event's local date, not UTC. The verdicts didn't change, and the numbers below are from the re-run.
- **FEMA's "Biological" declarations (COVID-19, 2020) covered every county.** They counted as storm exposure until they were filtered out. Fire declarations were removed too. The share of contracts "exposed" to a FEMA declaration fell from 58% to 36%.
- **Reading weather in contract text: "the weather made the work"** is not "the weather held the work up". These matched at first:
  - "Hurricane protection project"
  - "storm damage repair"
  - "temporary emergency power – Hurricane Milton"
  - "lightning protection system"
  - "sally port" (a secure gate, not Hurricane Sally)

  A change now counts only when it names weather together with a delay word (delay, extension, impacts, suspension, "due to"). Weather-citing contracts went from 180 to 98, and a hand check of samples found them clean.
- **Storm kinds matched inside other words:** "ice" in "notice", "rain" in "drainage", "wind" in "window". The patterns now match whole words only.
- **Planned option years aren't slips.** Exercising an option moves a contract's end date as planned, so only end-date moves from other kinds of change count as a slip.

---

## Experiment 1: did weather cause the crew contract to fail?

**Hypothesis (team):** when a crew contract fails and a bad revenue quarter follows, a severe weather event near the work during the contract caused it.

```powershell
python -m ml.contract_disruption     # writes contract_weather_attribution and ml/models/contract_disruption.json
```

### Process
1. **Checked the data first.** The database had no crew contracts at all (`crew_trips`: 0 rows), no record of which contracts failed, and revenue only for utilities. The weather data was complete. Public stand-ins were found and tested before building (table above).
2. **Defined failure.** Disrupted means terminated (default, cause, convenience or cancelled) or finished 30+ days late, not counting planned option years. 2,395 of the 7,049 fully observed contracts qualify: 93 terminated and 2,331 late.
3. **Linked weather to each contract** by county, during its **originally planned** window. The stretched window would be circular: a late contract runs longer and collects more storms because of the delay.
4. **Tested the rule against contracts without weather.** A logistic model of disruption used one exposed/not flag per weather type, controlling for planned length, contract value, type of work, agency, state, start year and start quarter. Odds ratios come with 95% intervals.
5. **Used the contracts' own words as evidence.** A change that names weather as the delay is **documented**. It's **documented + corroborated** when the change names a kind of weather (hurricane, flooding...) and NOAA recorded that kind in the county while the contract ran. A storm that merely overlapped is **storm overlap only** and is never counted as the cause.
6. **Revenue:** compared each company's quarterly revenue growth (vs the same quarter a year earlier) with storm days in its territory, same quarter and next quarter.

### Results
- **Overlap alone looks convincing.** Contracts with a tornado in their county were disrupted **43.6%** of the time, against **30.5%** without.
- **Once contracts are compared fairly, the effect mostly disappears.** Longer contracts both see more storms and slip more. With the controls, no weather type significantly raises the odds of disruption:
  - Tornado odds ratio 1.12, 95% interval 0.98–1.27.
  - Tropical 1.05 (0.91–1.22).
  - Thunderstorm wind 1.00 (0.87–1.14).
- **Weather adds nothing to predicting failure:** cross-validated AUC is 0.704 with the weather columns and 0.706 without them.
- **The original rule would be wrong about 95% of the time.** Of 809 disrupted contracts with a tornado nearby, **4.6%** say in their own records that weather caused the delay. The rate for all disrupted contracts is 3.8%.
- **91 disruptions are attributable to weather from evidence.**
  - **16 are documented + corroborated:** 12 tropical and 4 flooding. Examples:
    - "HURRICANE IAN IMPACTS", where NOAA records a tropical event in the same county on 2022-09-28, Ian's landfall.
    - "REA 0002 – FLOODING DELAYS 2021".
  - **75 are documented** as general weather delays ("WEATHER TIME EXTENSION").
- **No "storm, then bad quarter" pattern for any company.** For crew contractors, storms tend to come with **more** revenue (restoration work). Primoris is the only significant result (next quarter, Spearman 0.42, p = 0.01), one of 24 tests, so it may be chance. The public contractors hold just 1 of these federal contracts, so a contract failure can't be traced to a company's revenue with public data.

### Limits and what would change the verdict
- Federal contracts are the same kind of field work as a utility's crew contracts, but not the same owner. Weather may matter more for line crews on energized equipment.
- Contract text under-reports weather, since many delays are filed as plain "time extensions". Documented attribution is therefore a lower bound.
- Storms are matched by county, which is coarse for a single job site.
- **To make it definitive:** load the utility's own crew contracts (start, planned end, actual end, site, contractor, outcome) in place of `data/field_contracts.csv` with the same columns. The same pipeline then runs unchanged at site precision.

Exposed in the app: `GET /economics/contract-disruption`, `GET /sites/{id}/contracts`, and the site card's **Economic impact** section, with "DOCUMENTED" or "DOCUMENTED + NOAA" badges.

---

## Experiment 2: is there a restoration "bounceback" after weather?

**Hypothesis (team):** for any weather, a bounceback period follows the impact, when business booms because of restoration work. **If true,** store the effect in the ML database and tell supervisors to expect extra business when that weather occurs. **If not,** report the results.

```powershell
python scraper/contract_data.py fema_national   # national major-disaster declarations (added for this test)
python -m ml.bounceback                         # writes weather_bounceback and ml/models/bounceback.json
```

### Process
Three independent tests. Each compares against "no storm", so seasons and region-wide booms can't pass as a bounce.

1. **A. Field-contract business in the hit county** (main test, most data).
   - Panel: all 293 FL/GA/AL counties × 129 months = 37,797 county-months.
   - Outcomes: new field contracts, and dollars obligated (new contracts plus changes to existing ones).
   - **Event study:** for each storm's first month in a county (no storm of that kind in the 3 months before), the outcome is followed from 3 months before to 12 months after.
   - **Bounce** = the average of months +1 to +6, minus the 3 months before.
   - The comparison removes each county's own calendar-month pattern and the same month across all counties. This is a difference-in-differences with seasonal controls.
   - 95% intervals come from bootstrapping the storm events (2,000 resamples).
   - Storm kinds tested: tropical, tornado, flood, hail, winter, and FEMA weather declarations. That's 761–1,410 storm onsets per kind.
2. **B. National crew contractors' revenue.**
   - Quarterly growth in revenue for the 8 public contractors (330 company-quarters), against the number of counties under major weather-disaster declarations nationwide that quarter and in the two before. They work nationwide, so national storms are what drive their workload.
   - Company fixed effects, robust standard errors.
   - **Bounce** = the effect over the next two quarters.
3. **C. The sites' utilities.** Monthly revenue growth (vs the same month a year earlier) around 57 tropical-storm onsets in each utility's own site counties.

### What went wrong first (and why it matters)
The first version of test A removed only each county's average level. It found one "supported" effect: **new contracts +31% in months 1–6 after a hail month**, with interval +0.025 to +0.099 contracts per county-month. A placebo test on the first data also passed: none of 200 runs with random storm months reached it.

The effect turned out to be a calendar artifact:
- Federal contract awards spike every September at the end of the fiscal year, and the spike is bigger in counties with many contracts.
- Hail season is spring, so months +4 to +6 land on that spike.
- Removing each county's own calendar-month pattern makes it vanish: **+0.007, interval −0.019 to +0.034.**

The naive results are kept in `bounceback.json` (`regional_naive_two_way`) as the cautionary example, and a test now checks the seasonal adjustment.

### Results (final method)
| Test | Weather | Bounce, months 1–6 (or next 2 quarters) | 95% interval | Supported |
|---|---|---|---|---|
| A. New field contracts in the county | tropical | +2.5% | −0.026 to +0.037 contracts/month | no |
| | tornado | +1.6% | −0.030 to +0.037 | no |
| | flood | −0.2% | −0.030 to +0.029 | no |
| | hail | +3.4% | −0.019 to +0.034 | no |
| | winter | −2.8% | −0.032 to +0.019 | no |
| | FEMA weather declaration | −1.8% | −0.032 to +0.024 | no |
| A. Dollars obligated in the county | tropical | +1.2% | −10% to +13% (log points) | no |
| | tornado | −3.6% | −16% to +8% | no |
| | flood | +2.4% | −10% to +14% | no |
| | hail | +4.2% | −7% to +15% | no |
| | winter | +3.8% | −7% to +15% | no |
| | FEMA weather declaration | −2.0% | −13% to +8% | no |
| B. National contractors' revenue growth | major disasters | +0.6 points per standard deviation more declarations | −1.6 to +2.9 (p = 0.59) | no |
| B. Utility parents' revenue growth (same test) | major disasters | −0.6 points | −2.8 to +1.6 (p = 0.60) | no |
| C. Site utilities' revenue growth | tropical | −0.8 points | −7.2 to +5.6 | no |

**Verdict: not supported.** No weather type is followed by a measurable rise in field-contract business in the hit county, in national crew contractors' revenue, or in the sites' utilities' revenue.

On the data's resolution:
- **New contracts:** test A could have detected a bounce of about **±15%**. The average is 0.2 new contracts per county-month, and the intervals are about ±0.03.
- **Dollars obligated:** about **±12%**.

A smaller bounce can't be ruled out, but there's no boom large enough to plan around. Because it isn't supported, **no "expect extra business" notice was added for supervisors.** The tested effects are stored in `weather_bounceback`, all with `supported = false`. If a re-run on company data finds a real effect, it will be marked `supported = true`, and the supervisor notice can be built on it.

### Limits and what would change the verdict
- **Federal contracts are planned months ahead.** Restoration after a storm is mostly utility and private emergency work that never appears in USAspending, and in the SEC data only blended with nationwide revenue. The restoration *workload* is real: in the EAGLE-I outage data, stormier weeks have more customer-hours without power at every operator (deseasonalized r = 0.37–0.72; see the Economic impact section in the README). It just doesn't show up as extra *business* in public financial data.
- National contractors' revenue blends regions and business lines. The large hurricanes are a handful of events per year.
- **To make it definitive:** a utility's or contractor's own weekly work orders or billed restoration hours by county. With those as the outcome, `ml/bounceback.py`'s event study runs unchanged.
