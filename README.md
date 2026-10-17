# SwampHacksProject2026
Our SwampHacks

## Setup (virtual environment)

Use one Python virtual environment (`.venv`) for the whole project, so everyone runs the same Python with the same packages. Without it, `python` can point at different installs: this project hit a `No module named 'psycopg'` error because `python` was 3.14 while the packages were installed for 3.13.

### One-time setup

From the project root:

**Windows (PowerShell)**
```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt -r scraper/requirements.txt
```

If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled on this system"), allow scripts for your user once, then activate again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**macOS / Linux**
```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt -r scraper/requirements.txt
```

Python 3.13 is what the project is tested with. Any 3.12 or newer should work.

### Every new terminal

Activate the environment first. Your prompt then starts with `(.venv)`, and plain `python` uses the project's packages:

```powershell
.venv\Scripts\Activate.ps1        # Windows
source .venv/bin/activate         # macOS / Linux
```

Check that it worked: `python -c "import sys; print(sys.executable)"` should print a path inside `.venv`.

### VS Code

Open the Command Palette (Ctrl+Shift+P), choose **Python: Select Interpreter**, and pick the one in `.venv`. New VS Code terminals will then activate it automatically.

### Other setup

- **Database:** the connection string goes in `.env` in the project root, as `DATABASE_URL=...`. Get it from a teammate privately, or see [Database (Tiger Data)](#database-tiger-data).
- **Git:** `.venv/` and `.env` are git-ignored. Never commit either one.
- **New packages:** after installing a package, add it to the matching `requirements.txt` (`backend/` or `scraper/`) so teammates get it too.

## Power plant scraper

[scraper/scrape_power_plants.py](scraper/scrape_power_plants.py) collects operating power plants for the states you choose (default: Florida, Georgia, Alabama). It writes one row per plant to [data/power_plants.csv](data/power_plants.csv) with location, county and time zone.

The last run found 694 plants: Florida 346, Georgia 267, Alabama 81.

### How to run

From the project root:

```powershell
python -m pip install -r scraper/requirements.txt
python scraper/scrape_power_plants.py
```

Use `python -m pip` rather than `pip`, so the packages go into the same Python that runs the script.

| Option | What it does |
|---|---|
| `--states FL GA AL TX` | States to collect, as two-letter codes (default: `FL GA AL`) |
| `--top 50` | Keep only the N largest plants per state, by capacity (default: all) |
| `--out path.csv` | Where to write the results (default: `data/power_plants.csv`) |

The first run takes a few minutes, mostly for about 700 county lookups. The EIA file and the county lookups are saved in `data/raw/`, which git ignores, so later runs are quick. The public OpenStreetMap server often returns 502/504 errors under load; the script retries those automatically.

To test the OSM matching and weather radius logic:

```powershell
cd scraper
python test_match.py
```

### Data sources

| Source | Used for |
|---|---|
| [EIA-860M](https://www.eia.gov/electricity/data/eia860m/) (monthly generator inventory) | Main source: plant ID, name, operator, county, latitude/longitude, capacity, technology, fuels. The script finds the newest monthly file by itself. |
| OpenStreetMap via the Overpass API (`power=plant`) | Cross-checks coordinates and fills any missing ones. Plants are matched by the EIA plant ID stored in OSM (`ref:US:EIA`), otherwise by the closest OSM plant within 2 km. |
| [FCC Area API](https://geo.fcc.gov/api/census/) | County and county FIPS code from each plant's coordinates |
| [timezonefinder](https://pypi.org/project/timezonefinder/) (works offline) | Time zone, for the 6 PM local briefing |

Not used:

- **HIFLD:** HIFLD Open went offline on August 26, 2025. Archived copies exist on [DataLumos](https://www.datalumos.org/datalumos/project/240526/version/V1/view) and the [Data Rescue Project](https://portal.datarescueproject.org/datasets/hifld-open-power-plants/), but that data stops at August 2024 and its power plant data came from EIA-860, so it would repeat the main source.
- **Annual EIA-860:** kept as a backup only. The monthly file has coordinates for every plant in FL, GA and AL, and any missing coordinates are filled from OSM.

### Output columns

| Column | Meaning |
|---|---|
| `state`, `eia_plant_id`, `name`, `operator` | Plant identity from EIA |
| `primary_technology` | Technology with the most capacity at the plant |
| `fuels` | EIA energy source codes, separated by `;` |
| `capacity_mw`, `generators` | Total nameplate capacity (MW) and number of generators |
| `lat`, `lon`, `coord_source` | Coordinates and where they came from (`eia860m` or `osm`) |
| `county`, `county_fips` | County from the FCC API |
| `eia_county`, `county_matches_eia` | County listed by EIA, and whether it agrees with the FCC county |
| `timezone` | Time zone name, e.g. `America/New_York` or `America/Chicago` |
| `osm_id`, `osm_name` | Matching OpenStreetMap plant |
| `osm_match` | `eia_ref` (matched by EIA ID), `nearest` (within 2 km) or `none` |
| `osm_distance_km` | Distance between the EIA and OSM locations |
| `coord_check` | `ok` (within 1 km), `review` (more than 1 km apart) or `no_osm` |

### Checking the results

OSM coverage varies, so check the flagged rows by hand. From the last run:

| Flag | Count | Notes |
|---|---|---|
| `coord_check = review` | 117 | 101 are within 5 km, which is normal for large solar farms. A few are more than 20 km apart and need checking (the worst is Kayak Solar, about 42 km). |
| `coord_check = no_osm` | 131 | Mostly small solar sites that aren't in OSM |
| `county_matches_eia = False` | 22 | Usually plants on or near a county line |

Florida's panhandle is correctly marked as Central time. Four Alabama plants near Columbus, GA show Eastern time: two have coordinates that fall in Harris County, GA, and the Phenix City area unofficially uses Eastern time.

## Weather casualties near power plants

[scraper/relate_weather.py](scraper/relate_weather.py) finds weather events with deaths or injuries within a set radius of each plant in `data/power_plants.csv`. Run the power plant scraper first, then:

```powershell
python scraper/relate_weather.py
```

| Option | What it does |
|---|---|
| `--miles 10` | Radius around each plant, in miles (default: 25) |
| `--start-year 2020` | Earliest event year (default: 2015, when the OSHA data begins) |
| `--cdc path.txt` | CDC WONDER export (default: `data/cdc_wonder_weather_deaths.txt`) |

The first run downloads about 180 MB into `data/raw/` and takes under a minute. Later runs reuse those files.

It writes two files:

- [data/plant_weather_summary.csv](data/plant_weather_summary.csv): one row per plant, with storm casualty events, deaths, injuries and the top 3 storm types within the radius, the number of weather-related OSHA reports, and CDC county weather deaths if you added the export.
- `data/plant_weather_events.csv`: one row per plant and nearby event, with date, type, deaths, injuries, distance in miles and the event narrative. A single event appears once for every plant within range, so don't add up deaths across plants. The file is about 11 MB.

Last run (25 miles, 2015 onward): 18,298 plant–event pairs from 1,732 distinct events. 689 of the 694 plants had at least one storm casualty event nearby, and 650 had a weather-related OSHA report nearby.

### Sources

| Source | What's used | How it's placed |
|---|---|---|
| [NOAA Storm Events](https://www.ncei.noaa.gov/access/storm-events-database/) (bulk CSV) | Every event with at least one direct or indirect death or injury | Begin/end coordinates. For tornado paths, the closer end is used. |
| NWS [zone–county file](https://www.weather.gov/gis/ZoneCounty) | Locates NOAA events reported by forecast zone, which have no coordinates. These include most hurricane, rip current and heat deaths. | Zone centroid within the radius, or the plant's county lies entirely inside the zone |
| [OSHA Severe Injury Reports](https://www.osha.gov/severe-injury-reports) | Reports whose event is environmental heat, environmental cold, temperature extremes or lightning. Each report counts as one injured worker. | Employer coordinates |
| [CDC WONDER](https://wonder.cdc.gov/) (optional, via `cdc_wonder.py`) | Deaths with an underlying cause of ICD-10 X30–X39, "Exposure to forces of nature", by county | Joined on the plant's county FIPS code |

`match` in the events file is `point` (coordinates) or `zone` (NWS zone). Zone matches are approximate, because a zone is about the size of a county.

849 NOAA casualty events nationwide couldn't be placed. These are marine zones, county events without coordinates, and zone numbers that have changed since the event.

geopandas and shapely aren't needed: every match is a point-to-point distance, and the standard library computes them in seconds.

### Adding CDC WONDER data

The WONDER API [can't return state or county data](https://wonder.cdc.gov/wonder/help/wonder-api.html) for deaths, so [scraper/cdc_wonder.py](scraper/cdc_wonder.py) fills in the web form in Chrome instead (Chrome must be installed):

```powershell
python scraper/cdc_wonder.py
python scraper/relate_weather.py
```

1. A Chrome window opens on the data use agreement for [Underlying Cause of Death, 2018–2024, Single Race](https://wonder.cdc.gov/ucd-icd10-expanded.html).
2. Read the terms and click **I Agree** yourself. The script waits up to 10 minutes (`--wait` changes this).
3. The script groups by county, picks the states in `data/power_plants.csv`, enters ICD-10 X30–X39 and exports the results to `data/cdc_wonder_weather_deaths.txt`.

Options: `--states AL FL` (default: every state in `power_plants.csv`) and `--icd "X30-X39"`.

You agree to the terms each time, because the agreement is yours, not the script's. The terms say:

- use the data only for health statistics and analysis;
- don't publish death counts of 9 or fewer, or rates based on them;
- don't try to identify anyone.

If the app shows these numbers, it must hide counts under 10. CDC already shows those as "Suppressed".

Last export (2018–2024): 632 weather deaths across the 293 counties in AL, FL and GA. 184 counties are suppressed. The highest counts are Fulton County, GA (68) and Lee County, FL (55).

#### Manual fallback

If the form changes and the script breaks, export by hand:

1. Open [Underlying Cause of Death, 2018–2024, Single Race](https://wonder.cdc.gov/ucd-icd10-expanded.html) and accept the data use terms.
2. Under **Group Results By**, choose **County**.
3. Under location, select **Alabama**, **Florida** and **Georgia**.
4. Pick the years you want.
5. Under cause of death, choose **ICD-10 Codes** and select **X30-X39 (Exposure to forces of nature)**. In the tree it's under External causes > Other external causes of accidental injury.
6. Tick **Export Results**, set the format to TSV, click **Send**, and save the file as `data/cdc_wonder_weather_deaths.txt`.
7. Run `python scraper/relate_weather.py` again.

CDC hides county counts under 10 and shows them as "Suppressed". Many counties will show that; the `cdc_suppressed_rows` column counts them.

## Crew hazard reports (seed data)

[scraper/crew_reports.py](scraper/crew_reports.py) turns OSHA Severe Injury Reports near the plants into seed data for the crew hazard reporting feature. Run the power plant scraper first, then:

```powershell
python scraper/crew_reports.py            # optional: --miles 25 (default: 50)
```

It writes [data/crew_reports.csv](data/crew_reports.csv): one row per report and category, with location, date, employer, injury details, the nearest plant and its distance, and OSHA's narrative. One report can fall into several categories.

| Category | How a report is matched | `basis` values |
|---|---|---|
| `ppe` (not wearing proper PPE) | The narrative says PPE or fall protection wasn't worn, or the injury is one PPE is meant to prevent: a fall to a lower level (fall protection) or a lost eye (eye protection) | `narrative`, `injury_type` |
| `heavy_machinery` (negligent heavy machinery use) | OSHA's injury source code is construction or excavating machinery, cranes, lifts or conveyors, or forklifts and other industrial vehicles; or a pedestrian was struck by a vehicle on a worksite | `source`, `event` |
| `slip_trip` (spills and trash left in walkways) | A same-level fall from a slip or trip. `narrative` means the report names a spill, wet floor, debris, trash, cord or hose. | `narrative`, `injury_type` |
| `osha_inspection` (violations of OSHA guidelines) | OSHA opened an inspection after the injury | `inspection_opened` |

Last run, within 50 miles of a plant:

| Category | Basis | Rows |
|---|---|---|
| `ppe` | narrative | 6 |
| `ppe` | injury_type | 3,774 |
| `heavy_machinery` | source | 3,304 |
| `heavy_machinery` | event | 182 |
| `slip_trip` | narrative | 238 |
| `slip_trip` | injury_type | 1,499 |
| `osha_inspection` | inspection_opened | 7,384 |

That's 16,387 rows from 13,265 reports.

Limitations:

- OSHA narratives describe what happened and rarely say what PPE was missing. Most `ppe` rows are injuries PPE is meant to prevent, not confirmed cases of missing PPE. Filter on `basis` if that matters.
- The reports don't say whether machinery was used negligently, only that machinery caused the injury.
- `osha_inspection` means OSHA inspected, not that it cited a violation. Cited violations are in the Department of Labor's OSHA enforcement API, which needs a free key from [dataportal.dol.gov](https://dataportal.dol.gov) (sign-in through login.gov). They can be joined on the `osha_inspection` number.

## Site weather history

[scraper/site_weather.py](scraper/site_weather.py) gets daily weather from 2015 onward for every plant, using its nearest NWS airport weather station (ASOS):

```powershell
python scraper/site_weather.py            # optional: --start 2020-01-01
```

The regional climate pages publish these same stations as PDFs, text bulletins and dropdown tools, with no download API:

- [FSU Florida Climate Center](https://climatecenter.fsu.edu/products-services/data) (North Florida and Panhandle)
- [NWS Melbourne](https://www.weather.gov/mlb/climate) (East Central Florida)
- [NWS Tampa Bay](https://www.weather.gov/tbw/climatesummaries) (West Central Florida)
- [NWS Miami](https://www.weather.gov/mfl/climate) (South Florida)

The [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/) archive serves the same observations as CSV, including the daily maximum heat index, and covers Georgia and Alabama too.

It writes two files:

- `data/station_weather_daily.csv`: one row per station per day, with high and low temperature, max heat index (`max_feel`), max dew point, relative humidity, rain, and peak wind and gusts.
- [data/plant_stations.csv](data/plant_stations.csv): each plant's nearest station and distance, NWS office, Florida climate region, and the average days per year with a heat index of 105°F or more, 2 inches or more of rain, and gusts of 40 knots or more.

To get daily weather for a plant, join `plant_stations.station` to `station_weather_daily.station`.

Only full NWS/FAA ASOS stations are used, meaning those IEM lists as having 1-minute data. IEM's networks also include smaller AWOS airport stations, and in FL/GA/AL those are unreliable:

- Most report rain on fewer than a third of days.
- Their dew points read high, giving two to five times the heat-index days of major airports nearby. Tampa Executive showed 77 days a year at a heat index of 105°F or more; Tampa International, about 10 miles away, showed 30.

Last run (October 16, 2021 to October 15, 2026):

- 74 stations, 134,974 station-days. 4% of days have no rain value, and 1% have no heat index.
- Plants are a median of 20 miles from their station (at most 72).
- Florida plants by climate region: 126 North/Panhandle, 113 West Central, 78 East Central, 29 South Florida.
- Medians per plant per year: 15 days with a heat index of 105°F or more, 3.7 days with 2 inches or more of rain, 5.8 days with gusts of 40 knots or more.
- The per-year rates count only days that have a value. A blank gust is counted as no gust.

The script makes one request per state network per calendar year, each covering every station that's nearest to a plant. That's 36 requests from 2015 onward. IEM blocks rapid requests, so the script waits 10 seconds between them (about 6 minutes in total) and retries if the server is busy. Downloads are saved in `data/raw/`, so an interrupted run picks up where it left off.

To download the files by hand instead, write the URL list and open each URL in a browser:

```powershell
python scraper/site_weather.py --start 2021-10-16 --urls data/iem_download_urls.txt
```

## Database (Tiger Data)

The app's database is a free Tiger Cloud service named `planit-control` (PostgreSQL 18 with TimescaleDB and PostGIS) in project `uzhf064twr`.

### Connect

The connection string, including the password, is in `.env` as `DATABASE_URL`. `.env` is git-ignored: never commit it, and share it with teammates privately. A teammate can also get their own copy:

```powershell
irm https://cli.tigerdata.com/install.ps1 | iex   # install the Tiger CLI
tiger auth login                                  # sign in (ask to be added to the project first)
tiger db uri g8sw3qbfno --with-password            # print the connection string
```

### Apply the schema and load sites

```powershell
python -m pip install "psycopg[binary]"
python db/setup_db.py
```

[db/setup_db.py](db/setup_db.py) applies [db/schema.sql](db/schema.sql), loads the 694 plants from `data/power_plants.csv` as `sites` along with their weather stations, checks the lightning countdown logic, and refreshes `site_risk_profile`. It's safe to re-run.

### Load history and tie sites to hazards

```powershell
python db/load_history.py
```

[db/load_history.py](db/load_history.py) does a full reload, so it's safe to re-run. It loads:

- NOAA storm events (2015 onward), their extra locations and their fatalities
- OSHA severe injury reports
- CDC county weather deaths
- daily station weather

It downloads any NOAA location or fatality files that are missing from `data/raw/`. To stay well under the free service's storage cap, it keeps only events within about 70 miles of the sites. Then it ties each site to its events (`site_hazard_events`) and refreshes `site_risk_profile`.

NOAA events without coordinates are placed at their NWS forecast zone's centroid, with `location_precision = 'zone'`. Tornado tracks are stored as paths, so distance is measured to the nearest point on the track.

### Hazard ranges are data

Every radius, threshold and countdown is a row in `hazard_categories`, so you can retune a range with an `UPDATE` instead of a code change. Re-running the setup never overwrites these edits.

| Category | Warn | Stop work | Live radius (warn / stop) | History radius | All-clear |
|---|---|---|---|---|---|
| `lightning` | 1 strike | 1 strike | 20 / 10 mi | 10 mi | 30 min, restarts with each strike |
| `extreme_heat` | heat index 103°F | 115°F | at site | 25 mi | |
| `extreme_cold` | wind chill ≤ 32°F | ≤ 15°F | at site | 25 mi | |
| `flooding` | 2 in overnight rain | 4 in | gauge or warning within 5 / 1 mi | 10 mi | |
| `high_wind` | gust 20 mph | 28 mph (aerial lift limit) | at site | 25 mi | 30 min, restarts |
| `severe_storm` | NWS warning | NWS warning | 25 / 10 mi | 25 mi | 30 min, restarts |
| `tropical` | NWS watch/warning | NWS warning | 100 / 50 mi | 50 mi | |
| `wildfire` | satellite detection | detection | 10 / 2 mi | 10 mi | |
| `soil_saturation` | 4 in of rain in 7 days | 8 in | at site | | |
| `dense_fog` | visibility 1 mi | 0.25 mi | at site | 10 mi | |
| `wildlife` | sighting | sighting | 1 / 0.1 mi | 2 mi | |
| `coastal` | (history only) | | | 5 mi | |
| `ppe`, `heavy_machinery`, `slip_trip`, `osha_inspection` | (crew categories) | | | 50 mi | |

For example, to widen the lightning stop-work radius to 15 miles:

```sql
UPDATE hazard_categories SET stop_radius_mi = 15 WHERE category = 'lightning';
```

### Tables

| Group | Tables |
|---|---|
| Sites | `sites` (power plants), `weather_stations`, `hazard_categories` |
| History | `storm_events` (NOAA details), `storm_event_locations`, `storm_fatalities` (one row per person, with `crew_relevant` for outdoor, heavy-equipment, vehicle and under-tree deaths), `weather_fatalities` (CDC by county), `osha_injuries`, `weather_daily` |
| Live feeds | `lightning_strikes`, `weather_observations`, `weather_forecasts`, `nws_alerts`, `river_gauges`, `river_gauge_readings`, `fire_detections`, `wildlife_sightings` |
| App | `crew_reports`, `site_alerts`, `hazard_scores` (ML 0–10), `daily_briefings` |
| Ties | `site_hazard_events` (table), `site_weather_days` (view) |

- **Event ties:** `site_hazard_events` has one row per site, category and event, from NOAA storm events, OSHA reports and crew reports. Each row includes the distance in miles, deaths, injuries and damage. An event is tied to a site when it's within that category's `history_radius_mi`. After changing a radius, run `SELECT refresh_site_hazard_events();` and then refresh the profile.
- **Weather-day ties:** `site_weather_days` lists each day the site's station crossed a category's warn or stop threshold, labeled `warning` or `stop_work`. It covers extreme heat (max heat index), extreme cold (minimum temperature, standing in for wind chill), flooding (daily rain), high wind (peak gust) and soil saturation (7-day rain total). It's a view, so threshold edits apply immediately.

- **Time-series:** the time-based tables are TimescaleDB hypertables. Lightning strikes, live observations and gauge readings are compressed after a few days, and lightning strikes are deleted after 90 days.
- **Alerts:** `raise_site_alert(site_id, category, level, at, value)` opens an alert, or extends an open alert for the same site and category, restarting its all-clear countdown if the category says to. `active_site_alerts` lists open alerts with the time remaining.
- **Risk profile:** `site_risk_profile` is a materialized view with one row per site and a set of columns per category. Examples are `lightning_events`, `lightning_deaths`, `heat_warn_days_per_year`, `flood_events`, `heavy_rain_days_per_year`, `severe_storm_injuries`, `tropical_deaths`, `storm_fatalities_crew_relevant`, `cdc_county_weather_deaths`, `ppe_osha_reports` and `machinery_osha_reports`. It's built from `site_hazard_events` and the station weather. Run `REFRESH MATERIALIZED VIEW site_risk_profile` after loading data or changing a range.

## Backend (FastAPI + live data worker)

The [backend/](backend/) folder has two processes that share the Tiger database:

| Process | Command (from the project root) | What it does |
|---|---|---|
| API | `python -m uvicorn backend.app:app --reload` | Serves the endpoints below. Interactive docs are at http://127.0.0.1:8000/docs. |
| Worker | `python -m backend.worker` | Pulls live NWS and lightning data on a schedule and writes the 6 PM briefings. Run **one** copy. |

```powershell
python -m pip install -r backend/requirements.txt
$env:NWS_USER_AGENT = "PlanIT-Control (your-team-email@example.com)"   # NWS asks every client for a contact
python -m backend.worker points        # one-time: looks up each site's NWS forecast grid and alert zones
python -m backend.worker               # then leave this running
python -m backend.test_backend         # parsing and threshold checks (no network needed)
```

### Worker schedule

| Job | How often | Source → table |
|---|---|---|
| `lightning` | every minute | GOES-19 GLM satellite flashes (AWS bucket `noaa-goes19`) → `lightning_strikes`. Flashes within a site's warn/stop radius call `raise_site_alert()`, which restarts the 30-minute all-clear on each new strike. |
| `alerts` | every 2 min | NWS active alerts for the sites' states → `nws_alerts`. Alerts in effect now raise site alerts that last until the NWS warning expires. Storm-based warnings use their polygon; zone and county alerts use the site's NWS zone. |
| `forecasts` | hourly (at :05) | NWS hourly gridpoint forecast for the next 48 h → `weather_forecasts`: temperature, heat index, wind chill, gusts, rain, chance of rain and thunder, visibility. One request per NWS grid cell. |
| `briefings` | every 15 min | Writes tomorrow's briefing for each site where it's 6:00–6:15 PM local time → `daily_briefings` |
| `points` | daily | Looks up the NWS grid and zones for any new site |

Which NWS alert names map to which hazard category, and whether they mean `warning` or `stop_work`, is stored as data in `hazard_categories.nws_warn_events` and `nws_stop_events`.

### Endpoints

| Method and path | Returns |
|---|---|
| `GET /health` | database time and site count |
| `GET /hazard-categories` | thresholds, radii and countdowns (for the frontend legend) |
| `GET /sites?state=FL&limit=100` | **Dashboard ranking**, sorted by latest hazard score, then active stop-work and warning alerts |
| `GET /sites/{id}` | site details, historical `risk_profile`, `active_alerts`, `latest_score` |
| `GET /sites/{id}/forecast?hours=48` | hourly forecast |
| `GET /sites/{id}/hazard-events?category=lightning` | historical events tied to the site |
| `GET /alerts/active?state=FL` | **Instant alerts** with `all_clear_seconds` for the countdown. Poll every 15–30 s. |
| `GET /briefings/{id}?date=2026-10-18` | the 6 PM next-day briefing: `summary` text plus structured `content` (flags, forecast, NWS alerts, score) for the AI API to rewrite |
| `POST /crew-reports` | `{category, lat, lon, description, photo_url, reported_by, severity, site_id?}`. Upload the photo to storage first and send its URL. The nearest site within 10 mi is filled in when `site_id` is omitted. |
| `GET /crew-reports?site_id=&category=&status=open` | crew hazard reports |

**Demo endpoints** (start the API with `$env:PLANIT_DEV = "1"`):

| Method and path | What it does |
|---|---|
| `POST /dev/lightning/{site_id}?miles_away=3` | fakes a strike to show the stop-work countdown |
| `POST /dev/briefings/{site_id}` | writes tomorrow's briefing now, without waiting for 6 PM |
| `POST /dev/jobs/{name}` | runs one worker job |

### Notes

- `hazard_scores` stays empty until the ML model writes to it. Until then, the dashboard ranks sites by active alerts, then capacity.
- GLM locates lightning to roughly 8 km (about 5 mi). That's coarse for a 10-mile rule, but it's the best free real-time source.
- Forecasts keep 30 days, NWS alerts keep 180 days, and lightning keeps 90 days (compressed after 2), to stay within the free tier.
