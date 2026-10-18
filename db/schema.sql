-- PlanIT Control schema for Tiger Data (TimescaleDB + PostGIS).
-- Safe to re-run: tables use IF NOT EXISTS, default rules never overwrite edits, derived views are rebuilt.
-- Distances are miles; geography columns are WGS84 lon/lat (SRID 4326).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ===========================================================================================================
-- Hazard rules: one row per category. Every radius, threshold and countdown lives here as data, so the
-- team can retune a range (e.g. lightning stop-work radius) with an UPDATE instead of a code change.
-- ===========================================================================================================
CREATE TABLE IF NOT EXISTS hazard_categories (
    category            text PRIMARY KEY,
    label               text NOT NULL,
    kind                text NOT NULL CHECK (kind IN ('environmental', 'crew')),
    metric              text,             -- live value the alert watches, e.g. strikes, heat_index_f
    unit                text,
    warn_value          numeric,          -- metric >= warn_value -> warning (<= for cold)
    stop_value          numeric,          -- metric >= stop_value -> stop work
    warn_radius_mi      numeric,          -- live event this close -> warning (NULL = measured at the site)
    stop_radius_mi      numeric,          -- live event this close -> stop work
    history_radius_mi   numeric NOT NULL, -- historical events this close count toward the site's risk profile
    window_minutes      int,              -- look-back for the metric, e.g. 720 = overnight rain
    all_clear_minutes   int,              -- stop-work lasts this long after the last trigger
    all_clear_restarts  boolean NOT NULL DEFAULT false,  -- each new trigger restarts the countdown
    noaa_event_types    text[] NOT NULL DEFAULT '{}',    -- NOAA Storm Events EVENT_TYPE values in this category
    notes               text,
    lower_is_worse      boolean NOT NULL DEFAULT false,  -- cold and fog: values at or below the thresholds trigger
    nws_warn_events     text[] NOT NULL DEFAULT '{}',    -- NWS alert names that raise a warning
    nws_stop_events     text[] NOT NULL DEFAULT '{}'     -- NWS alert names that raise stop work
);
ALTER TABLE hazard_categories ADD COLUMN IF NOT EXISTS lower_is_worse boolean NOT NULL DEFAULT false;
ALTER TABLE hazard_categories ADD COLUMN IF NOT EXISTS nws_warn_events text[] NOT NULL DEFAULT '{}';
ALTER TABLE hazard_categories ADD COLUMN IF NOT EXISTS nws_stop_events text[] NOT NULL DEFAULT '{}';

INSERT INTO hazard_categories VALUES
('lightning', 'Lightning', 'environmental', 'strikes', 'count', 1, 1, 20, 10, 10, 30, 30, true,
 '{Lightning}', 'Any strike within 10 mi: stop work, 30-min all-clear that restarts with each new strike. Warn at 20 mi.'),
('extreme_heat', 'Extreme heat', 'environmental', 'heat_index_f', 'F', 103, 115, NULL, NULL, 25, 60, NULL, false,
 '{Heat,"Excessive Heat"}', 'OSHA heat index risk bands: 103-115 high, above 115 very high/extreme.'),
('extreme_cold', 'Extreme cold', 'environmental', 'wind_chill_f', 'F', 32, 15, NULL, NULL, 25, 60, NULL, false,
 '{Cold/Wind Chill,"Extreme Cold/Wind Chill",Frost/Freeze,"Winter Storm","Winter Weather","Ice Storm","Heavy Snow",Sleet,"Freezing Fog"}',
 'Lower is worse: warn at or below 32 F, stop at or below 15 F.'),
('flooding', 'Flooding', 'environmental', 'rain_in', 'in', 2, 4, 5, 1, 10, 720, NULL, false,
 '{Flood,"Flash Flood","Heavy Rain","Coastal Flood","Lakeshore Flood","Storm Surge/Tide"}',
 'Overnight (12 h) rain at the site, or a river gauge above flood stage within the radius, or an NWS flood warning covering the site.'),
('high_wind', 'High wind', 'environmental', 'wind_gust_mph', 'mph', 20, 28, NULL, NULL, 25, 10, 30, true,
 '{"High Wind","Strong Wind"}', '28 mph is the ANSI A92 aerial-lift limit; cranes follow the manufacturer chart.'),
('severe_storm', 'Severe thunderstorm / tornado', 'environmental', 'nws_warning', 'active', 1, 1, 25, 10, 25, NULL, 30, true,
 '{"Thunderstorm Wind",Tornado,Hail,"Funnel Cloud",Waterspout,"Dust Devil"}',
 'NWS severe thunderstorm or tornado warning polygon within the radius.'),
('tropical', 'Tropical storm / hurricane', 'environmental', 'nws_warning', 'active', 1, 1, 100, 50, 50, NULL, NULL, false,
 '{Hurricane,"Hurricane (Typhoon)","Tropical Storm","Tropical Depression"}',
 'Tropical storm or hurricane watch/warning for the site''s zone.'),
('wildfire', 'Wildfire', 'environmental', 'fire_detections', 'count', 1, 1, 10, 2, 10, 1440, NULL, false,
 '{Wildfire}', 'Satellite fire detection (NASA FIRMS) in the last 24 h.'),
('soil_saturation', 'Soil saturation / mud', 'environmental', 'rain_7d_in', 'in', 4, 8, NULL, NULL, 0, 10080, NULL, false,
 '{}', 'A week of soaking rain: soft ground, stuck vehicles, unstable trench walls.'),
('dense_fog', 'Dense fog / smoke', 'environmental', 'visibility_mi', 'mi', 1, 0.25, NULL, NULL, 10, 60, NULL, false,
 '{"Dense Fog","Dense Smoke"}', 'Lower is worse: equipment moves and site access.'),
('coastal', 'Rip current / high surf', 'environmental', NULL, NULL, NULL, NULL, NULL, NULL, 5, NULL, NULL, false,
 '{"Rip Current","High Surf",Sneakerwave}', 'History only; matters for waterfront sites.'),
('wildlife', 'Wildlife sighting', 'environmental', 'sightings', 'count', 1, 1, 1, 0.1, 2, 4320, NULL, false,
 '{}', 'Wildlife observations within a 3-day relevance window; expiry does not establish that an animal left.'),
('ppe', 'PPE not worn', 'crew', NULL, NULL, NULL, NULL, NULL, NULL, 50, NULL, NULL, false, '{}', 'OSHA reports + crew reports.'),
('heavy_machinery', 'Heavy machinery misuse', 'crew', NULL, NULL, NULL, NULL, NULL, NULL, 50, NULL, NULL, false, '{}', 'OSHA reports + crew reports.'),
('slip_trip', 'Spills / trash in walkways', 'crew', NULL, NULL, NULL, NULL, NULL, NULL, 50, NULL, NULL, false, '{}', 'OSHA reports + crew reports.'),
('osha_inspection', 'OSHA violation / inspection', 'crew', NULL, NULL, NULL, NULL, NULL, NULL, 50, NULL, NULL, false, '{}', 'OSHA opened an inspection.')
ON CONFLICT (category) DO NOTHING;  -- keep the team's edits on re-run
-- high_wind watches sustained wind (the aerial-lift limit is a sustained-wind limit). Daily peak gusts of 28 mph
-- happen on ~26% of station-days, so gusts made a quarter of all days look like stop-work days.
UPDATE hazard_categories SET metric = 'wind_mph',
       notes = 'Sustained wind. 28 mph is the ANSI A92 aerial-lift limit; cranes follow the manufacturer chart.'
 WHERE category = 'high_wind' AND metric = 'wind_gust_mph' AND warn_value = 20 AND stop_value = 28;
UPDATE hazard_categories SET lower_is_worse = true
 WHERE category IN ('extreme_cold', 'dense_fog') AND NOT lower_is_worse;

-- Default NWS alert -> category mapping; only fills categories whose lists are still empty.
UPDATE hazard_categories h SET nws_warn_events = d.warn, nws_stop_events = d.stop
  FROM (VALUES
    ('severe_storm', '{"Severe Thunderstorm Watch","Tornado Watch"}'::text[],
                     '{"Severe Thunderstorm Warning","Tornado Warning"}'::text[]),
    ('tropical',     '{"Tropical Storm Watch","Hurricane Watch","Storm Surge Watch"}',
                     '{"Tropical Storm Warning","Hurricane Warning","Storm Surge Warning","Extreme Wind Warning"}'),
    ('flooding',     '{"Flood Watch","Flash Flood Watch","Flood Advisory","Flood Warning","Coastal Flood Watch","Coastal Flood Advisory","Coastal Flood Warning"}',
                     '{"Flash Flood Warning","Flash Flood Emergency"}'),
    ('extreme_heat', '{"Heat Advisory","Excessive Heat Watch","Extreme Heat Watch"}',
                     '{"Excessive Heat Warning","Extreme Heat Warning"}'),
    ('extreme_cold', '{"Cold Weather Advisory","Wind Chill Advisory","Winter Weather Advisory","Winter Storm Watch"}',  -- frost/freeze products are for crops
                     '{"Extreme Cold Warning","Wind Chill Warning","Winter Storm Warning","Ice Storm Warning","Blizzard Warning"}'),
    ('high_wind',    '{"Wind Advisory","High Wind Watch"}', '{"High Wind Warning"}'),
    ('wildfire',     '{"Red Flag Warning","Fire Weather Watch"}', '{"Fire Warning"}'),
    ('dense_fog',    '{"Dense Fog Advisory","Dense Smoke Advisory"}', '{}'),
    ('coastal',      '{"High Surf Advisory"}', '{"High Surf Warning"}')  -- rip currents and beach hazards are for swimmers, not crews
  ) AS d(category, warn, stop)
 WHERE h.category = d.category AND h.nws_warn_events = '{}' AND h.nws_stop_events = '{}';

-- Upgrade the old defaults on existing databases, while preserving other custom thresholds/event mappings.
UPDATE hazard_categories SET nws_warn_events = array_remove(array_remove(nws_warn_events,
    'Rip Current Statement'), 'Beach Hazards Statement') WHERE category = 'coastal';
UPDATE hazard_categories SET nws_warn_events = array_remove(nws_warn_events, 'Lake Wind Advisory')
 WHERE category = 'high_wind';
UPDATE hazard_categories SET window_minutes = 4320,
    notes = 'Wildlife observations within a 3-day relevance window; expiry does not establish that an animal left.'
 WHERE category = 'wildlife' AND window_minutes = 10080;

-- ===========================================================================================================
-- Job sites (power plants) and weather stations
-- ===========================================================================================================
CREATE TABLE IF NOT EXISTS weather_stations (
    station_id   text PRIMARY KEY,         -- ASOS id, e.g. MCO
    name         text,
    network      text,                     -- e.g. FL_ASOS
    nws_office   text,
    geog         geography(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS sites (
    site_id               bigint PRIMARY KEY,   -- EIA plant ID
    name                  text NOT NULL,
    operator              text,
    state                 char(2) NOT NULL,
    county                text,
    county_fips           char(5),
    timezone              text NOT NULL,        -- IANA, drives the 6 PM local briefing
    capacity_mw           numeric,
    primary_technology    text,
    fuels                 text,
    generators            int,
    geog                  geography(Point, 4326) NOT NULL,
    coord_source          text,                 -- eia860m or osm
    osm_id                text,
    coord_check           text,                 -- ok / review / no_osm
    nws_office            text,
    fl_climate_region     text,
    weather_station_id    text REFERENCES weather_stations,
    weather_station_mi    numeric
);
CREATE INDEX IF NOT EXISTS sites_geog_idx ON sites USING gist (geog);
-- NWS lookups (filled once per site by the worker's "points" job)
ALTER TABLE sites ADD COLUMN IF NOT EXISTS nws_grid_id   text;    -- forecast office grid, e.g. MLB
ALTER TABLE sites ADD COLUMN IF NOT EXISTS nws_grid_x    int;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS nws_grid_y    int;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS forecast_zone text;    -- e.g. FLZ045, matches NWS alert UGC codes
ALTER TABLE sites ADD COLUMN IF NOT EXISTS county_ugc    text;    -- e.g. FLC095
-- FEMA National Flood Hazard Layer at the site (scraper/fema_flood_zones.py)
ALTER TABLE sites ADD COLUMN IF NOT EXISTS fema_flood_zone   text;     -- AE, VE, A, X, ... or 'unmapped'
ALTER TABLE sites ADD COLUMN IF NOT EXISTS fema_zone_subtype text;     -- e.g. 0.2 PCT ANNUAL CHANCE FLOOD HAZARD
ALTER TABLE sites ADD COLUMN IF NOT EXISTS fema_sfha         boolean;  -- inside the 1%-a-year (100-year) floodplain
ALTER TABLE sites ADD COLUMN IF NOT EXISTS fema_bfe_ft       numeric;  -- base flood elevation, where FEMA set one

-- ===========================================================================================================
-- History: NOAA Storm Events (details, locations, fatalities), CDC weather deaths, OSHA injuries, daily weather
-- ===========================================================================================================
CREATE TABLE IF NOT EXISTS storm_events (                -- StormEvents_details
    event_id            bigint NOT NULL,
    episode_id          bigint,
    begin_time          timestamptz NOT NULL,
    end_time            timestamptz,
    state               text,
    state_fips          char(2),
    event_type          text NOT NULL,
    category            text REFERENCES hazard_categories,  -- from hazard_categories.noaa_event_types
    cz_type             char(1),                             -- C county, Z forecast zone, M marine
    cz_fips             text,
    cz_name             text,
    wfo                 text,
    injuries_direct     int NOT NULL DEFAULT 0,
    injuries_indirect   int NOT NULL DEFAULT 0,
    deaths_direct       int NOT NULL DEFAULT 0,
    deaths_indirect     int NOT NULL DEFAULT 0,
    damage_property_usd numeric,
    damage_crops_usd    numeric,
    source              text,
    magnitude           numeric,                             -- wind kt or hail inches
    magnitude_type      text,
    flood_cause         text,
    tropical_category   int,
    tor_f_scale         text,
    tor_length_mi       numeric,
    tor_width_yd        numeric,
    begin_location      text,
    end_location        text,
    geog                geography(Geometry, 4326),           -- point, tornado path line, or zone centroid
    location_precision  text CHECK (location_precision IN ('point', 'path', 'zone', 'none')),
    episode_narrative   text,
    event_narrative     text,
    PRIMARY KEY (event_id, begin_time)
);
SELECT create_hypertable('storm_events', by_range('begin_time', INTERVAL '6 months'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS storm_events_geog_idx ON storm_events USING gist (geog);
CREATE INDEX IF NOT EXISTS storm_events_category_idx ON storm_events (category, begin_time DESC);

CREATE TABLE IF NOT EXISTS storm_event_locations (       -- StormEvents_locations: extra points along an event
    event_id        bigint NOT NULL,
    episode_id      bigint,
    location_index  int NOT NULL,
    year_month      int NOT NULL,                        -- YYYYMM
    range_mi        numeric,
    azimuth         text,
    location        text,
    geog            geography(Point, 4326) NOT NULL,
    PRIMARY KEY (event_id, location_index)
);
CREATE INDEX IF NOT EXISTS storm_event_locations_geog_idx ON storm_event_locations USING gist (geog);

CREATE TABLE IF NOT EXISTS storm_fatalities (            -- StormEvents_fatalities: one row per person
    fatality_id     bigint NOT NULL,
    event_id        bigint NOT NULL,
    fatality_time   timestamptz NOT NULL,
    is_direct       boolean NOT NULL,                    -- FATALITY_TYPE D (direct) / I (indirect)
    age             int,
    sex             char(1),
    location        text,                                -- e.g. Outside/Open Areas, Heavy Equipment/Construction
    crew_relevant   boolean GENERATED ALWAYS AS (location IN
                        ('Heavy Equipment/Construction', 'Outside/Open Areas', 'Under Tree', 'Vehicle/Towed Trailer')) STORED,
    PRIMARY KEY (fatality_id, fatality_time)
);
SELECT create_hypertable('storm_fatalities', by_range('fatality_time', INTERVAL '1 year'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS storm_fatalities_event_idx ON storm_fatalities (event_id);

CREATE TABLE IF NOT EXISTS weather_fatalities (          -- CDC WONDER: deaths by county, ICD-10 X30-X39
    county_fips     char(5) NOT NULL,
    county_name     text,
    period_start    date NOT NULL,
    period_end      date NOT NULL,
    icd10_codes     text NOT NULL,
    deaths          int,                                 -- NULL when suppressed (under 10): never display a value
    suppressed      boolean NOT NULL DEFAULT false,
    population      bigint,
    crude_rate      numeric,
    PRIMARY KEY (county_fips, period_start, period_end, icd10_codes)
);

CREATE TABLE IF NOT EXISTS osha_injuries (               -- OSHA Severe Injury Reports
    report_id           bigint NOT NULL,
    event_time          timestamptz NOT NULL,
    employer            text,
    address             text,
    city                text,
    state               text,
    zip                 text,
    naics               text,
    hospitalized        int,
    amputation          int,
    loss_of_eye         int,
    inspection_id       text,
    nature              text,
    part_of_body        text,
    event_code          text,
    event_title         text,
    source_code         text,
    source_title        text,
    narrative           text,
    crew_categories     text[] NOT NULL DEFAULT '{}',    -- ppe / heavy_machinery / slip_trip / osha_inspection
    weather_category    text REFERENCES hazard_categories,  -- extreme_heat / extreme_cold / lightning, if weather-caused
    geog                geography(Point, 4326),
    PRIMARY KEY (report_id, event_time)
);
SELECT create_hypertable('osha_injuries', by_range('event_time', INTERVAL '1 year'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS osha_injuries_geog_idx ON osha_injuries USING gist (geog);

CREATE TABLE IF NOT EXISTS weather_daily (               -- IEM ASOS daily summaries
    station_id          text NOT NULL REFERENCES weather_stations,
    day                 date NOT NULL,
    max_temp_f          numeric,
    min_temp_f          numeric,
    max_heat_index_f    numeric,
    max_dewpoint_f      numeric,
    min_rh              numeric,
    avg_rh              numeric,
    precip_in           numeric,
    max_gust_kt         numeric,
    max_wind_kt         numeric,
    PRIMARY KEY (station_id, day)
);
SELECT create_hypertable('weather_daily', by_range('day', INTERVAL '1 year'), if_not_exists => true);

-- ===========================================================================================================
-- Live feeds (high volume -> columnstore compression and retention where it makes sense)
-- ===========================================================================================================
CREATE TABLE IF NOT EXISTS lightning_strikes (
    observed_at     timestamptz NOT NULL,
    geog            geography(Point, 4326) NOT NULL,
    source          text NOT NULL DEFAULT 'goes_glm',
    energy_fj       numeric,                             -- GLM flash energy (femtojoules)
    quality_flag    int
);
SELECT create_hypertable('lightning_strikes', by_range('observed_at', INTERVAL '1 day'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS lightning_strikes_geog_idx ON lightning_strikes USING gist (geog);

CREATE TABLE IF NOT EXISTS weather_observations (       -- live conditions per site (NWS API / station)
    site_id         bigint NOT NULL REFERENCES sites,
    observed_at     timestamptz NOT NULL,
    temp_f          numeric,
    heat_index_f    numeric,
    wind_chill_f    numeric,
    dewpoint_f      numeric,
    rh              numeric,
    wind_mph        numeric,
    wind_gust_mph   numeric,
    precip_1h_in    numeric,
    visibility_mi   numeric,
    soil_moisture   numeric,                             -- m3/m3, 0-7 cm
    source          text,
    PRIMARY KEY (site_id, observed_at)
);
SELECT create_hypertable('weather_observations', by_range('observed_at', INTERVAL '7 days'), if_not_exists => true);
SELECT add_retention_policy('weather_observations', INTERVAL '8 days', if_not_exists => true);  -- the scanner shows 7

CREATE TABLE IF NOT EXISTS weather_forecasts (           -- next-day forecast for the 6 PM briefing
    site_id         bigint NOT NULL REFERENCES sites,
    issued_at       timestamptz NOT NULL,
    valid_at        timestamptz NOT NULL,
    temp_f          numeric,
    heat_index_f    numeric,
    precip_chance   numeric,
    precip_in       numeric,
    wind_gust_mph   numeric,
    thunder_chance  numeric,
    short_forecast  text,
    PRIMARY KEY (site_id, valid_at, issued_at)
);
SELECT create_hypertable('weather_forecasts', by_range('valid_at', INTERVAL '7 days'), if_not_exists => true);
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS wind_chill_f  numeric;
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS visibility_mi numeric;
ALTER TABLE weather_forecasts ADD COLUMN IF NOT EXISTS wind_mph      numeric;  -- sustained
SELECT add_retention_policy('weather_forecasts', INTERVAL '8 days', if_not_exists => true);

CREATE TABLE IF NOT EXISTS nws_alerts (
    alert_id        text NOT NULL,
    sent_at         timestamptz NOT NULL,
    event           text NOT NULL,                       -- e.g. Flood Warning, Tornado Warning
    category        text REFERENCES hazard_categories,
    severity        text,
    urgency         text,
    onset_at        timestamptz,
    expires_at      timestamptz,
    headline        text,
    ugc_codes       text[],
    geog            geography(Geometry, 4326),           -- warning polygon when given
    PRIMARY KEY (alert_id, sent_at)
);
SELECT create_hypertable('nws_alerts', by_range('sent_at', INTERVAL '1 month'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS nws_alerts_geog_idx ON nws_alerts USING gist (geog);
SELECT add_retention_policy('nws_alerts', INTERVAL '180 days', if_not_exists => true);

CREATE TABLE IF NOT EXISTS river_gauges (
    gauge_id            text PRIMARY KEY,                -- NWS gauge id (NOAA NWPS "lid"), e.g. AANG1
    name                text,
    geog                geography(Point, 4326) NOT NULL,
    action_stage_ft     numeric,
    flood_stage_ft      numeric,
    moderate_stage_ft   numeric,
    major_stage_ft      numeric
);
CREATE INDEX IF NOT EXISTS river_gauges_geog_idx ON river_gauges USING gist (geog);

CREATE TABLE IF NOT EXISTS river_gauge_readings (
    gauge_id        text NOT NULL REFERENCES river_gauges,
    observed_at     timestamptz NOT NULL,
    stage_ft        numeric,
    flow_cfs        numeric,
    PRIMARY KEY (gauge_id, observed_at)
);
SELECT create_hypertable('river_gauge_readings', by_range('observed_at', INTERVAL '7 days'), if_not_exists => true);
-- NWS flood categories: no_flooding / action / minor / moderate / major (computed by NOAA from each gauge's stages)
ALTER TABLE river_gauge_readings ADD COLUMN IF NOT EXISTS flood_category          text;
ALTER TABLE river_gauge_readings ADD COLUMN IF NOT EXISTS forecast_flood_category text;
ALTER TABLE river_gauge_readings ADD COLUMN IF NOT EXISTS forecast_stage_ft       numeric;

CREATE TABLE IF NOT EXISTS fire_detections (             -- NASA FIRMS
    detected_at     timestamptz NOT NULL,
    geog            geography(Point, 4326) NOT NULL,
    satellite       text,
    confidence      text,
    frp_mw          numeric                              -- fire radiative power
);
SELECT create_hypertable('fire_detections', by_range('detected_at', INTERVAL '7 days'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS fire_detections_geog_idx ON fire_detections USING gist (geog);

CREATE TABLE IF NOT EXISTS wildlife_sightings (
    observed_at     timestamptz NOT NULL,
    geog            geography(Point, 4326) NOT NULL,
    species         text,                                -- scientific name, e.g. Alligator mississippiensis
    common_name     text,
    source          text NOT NULL,                       -- inaturalist / crew
    source_ref      text,
    photo_url       text,
    PRIMARY KEY (source, source_ref, observed_at)
);
SELECT create_hypertable('wildlife_sightings', by_range('observed_at', INTERVAL '1 month'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS wildlife_sightings_geog_idx ON wildlife_sightings USING gist (geog);

-- ===========================================================================================================
-- App tables: crew reports, alerts with countdowns, ML scores, 6 PM briefings
-- ===========================================================================================================
CREATE TABLE IF NOT EXISTS crew_reports (
    report_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reported_at     timestamptz NOT NULL DEFAULT now(),
    site_id         bigint REFERENCES sites,
    category        text NOT NULL REFERENCES hazard_categories,
    description     text,
    photo_url       text,
    geog            geography(Point, 4326) NOT NULL,
    reported_by     text,
    severity        int CHECK (severity BETWEEN 1 AND 5),
    status          text NOT NULL DEFAULT 'open',      -- open / resolved / dismissed / needs_review (constraint below)
    resolved_at     timestamptz
);
CREATE INDEX IF NOT EXISTS crew_reports_geog_idx ON crew_reports USING gist (geog);
CREATE INDEX IF NOT EXISTS crew_reports_site_idx ON crew_reports (site_id, reported_at DESC);

-- Users (Auth0 sign-in) and where each wants alerts sent. A user row is created the first time a verified Auth0
-- token reaches the API; no passwords are stored here. Webhook URLs are secrets, so they're stored encrypted with a
-- key that lives only in the app's environment (backend/notify.py), and the API shows just the last 4 characters.
CREATE TABLE IF NOT EXISTS app_users (
    user_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auth0_sub       text NOT NULL UNIQUE,                -- the token's "sub", e.g. google-oauth2|1234
    email           text,
    name            text,
    role            text NOT NULL DEFAULT 'crew' CHECK (role IN ('crew', 'supervisor')),  -- not enforced yet
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notification_channels (
    channel_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           uuid NOT NULL REFERENCES app_users ON DELETE CASCADE,
    kind              text NOT NULL CHECK (kind IN ('discord', 'slack')),
    label             text CHECK (length(label) <= 80),
    webhook_encrypted text NOT NULL,                     -- Fernet token, never the plain URL
    webhook_hint      text NOT NULL,                     -- last 4 characters, to tell channels apart
    levels            text[] NOT NULL DEFAULT '{stop_work}' CHECK (levels <@ '{warning,stop_work}'),
    site_ids          bigint[],                          -- NULL = every site
    briefings         boolean NOT NULL DEFAULT true,
    crew_reports      boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_sent_at      timestamptz,
    last_error        text
);
CREATE INDEX IF NOT EXISTS notification_channels_user_idx ON notification_channels (user_id);
ALTER TABLE notification_channels ADD COLUMN IF NOT EXISTS identity text NOT NULL DEFAULT 'crew' CHECK (identity IN ('crew', 'supervisor'));
CREATE TABLE IF NOT EXISTS notification_log (            -- what each channel has been sent, so nothing repeats
    channel_id      bigint NOT NULL REFERENCES notification_channels ON DELETE CASCADE,
    item            text NOT NULL,                       -- alert:<id> / report:<uuid> / briefing:<site>:<date>
    sent_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel_id, item)
);
-- A user's saved starting points; each gets its own 6 PM briefing (backend/briefings.py) for the plants in range.
CREATE TABLE IF NOT EXISTS saved_locations (
    location_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES app_users ON DELETE CASCADE,
    name            text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    geog            geography(Point, 4326) NOT NULL,
    radius_mi       numeric NOT NULL CHECK (radius_mi BETWEEN 1 AND 300),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS saved_locations_user_idx ON saved_locations (user_id);
ALTER TABLE saved_locations ADD COLUMN IF NOT EXISTS site_id bigint REFERENCES sites ON DELETE SET NULL;  -- saved from a site card
-- Identities: one person can work as crew and as supervisor; each identity keeps its own saved locations,
-- notification channels and report list. The identity is picked in the app (X-Planit-Role header); supervisor
-- *permissions* still need approval (app_users.role = 'supervisor' or SUPERVISOR_EMAILS, backend/auth.py).
ALTER TABLE saved_locations ADD COLUMN IF NOT EXISTS identity text NOT NULL DEFAULT 'crew' CHECK (identity IN ('crew', 'supervisor'));
DROP INDEX IF EXISTS saved_locations_user_site_idx;
CREATE UNIQUE INDEX IF NOT EXISTS saved_locations_user_identity_site_idx ON saved_locations (user_id, identity, site_id)
    WHERE site_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS location_briefings (
    location_id     bigint NOT NULL REFERENCES saved_locations ON DELETE CASCADE,
    briefing_date   date NOT NULL,                       -- the day it's about (tomorrow, at 6 PM)
    summary         text NOT NULL,
    content         jsonb NOT NULL,
    sent_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (location_id, briefing_date)
);
-- Your briefing (backend/team_briefing.py): Gemini text across your starting area and saved locations, with crew
-- messages drafted for a supervisor's channels. Cached 2 h per identity, kind and starting point.
CREATE TABLE IF NOT EXISTS user_briefings (
    briefing_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             uuid NOT NULL REFERENCES app_users ON DELETE CASCADE,
    identity            text NOT NULL CHECK (identity IN ('crew', 'supervisor')),
    kind                text NOT NULL CHECK (kind IN ('start', '6pm')),
    focus               jsonb NOT NULL,                  -- lat, lon, radius_mi, label (null lat = all sites)
    created_at          timestamptz NOT NULL DEFAULT now(),
    generator           text NOT NULL,                   -- gemini:<model> or template
    facts               jsonb NOT NULL,                  -- exactly what the model was given
    report              jsonb NOT NULL,
    verified            boolean NOT NULL,                -- false when the model wrote numbers not in the facts
    unverified_numbers  text[],
    errors              text[]
);
CREATE INDEX IF NOT EXISTS user_briefings_user_idx ON user_briefings (user_id, identity, kind, created_at DESC);
ALTER TABLE crew_reports ADD COLUMN IF NOT EXISTS user_id     uuid REFERENCES app_users;
ALTER TABLE crew_reports ADD COLUMN IF NOT EXISTS resolved_by uuid REFERENCES app_users;
ALTER TABLE crew_reports ADD COLUMN IF NOT EXISTS reported_as text CHECK (reported_as IN ('crew', 'supervisor'));
ALTER TABLE crew_reports ADD COLUMN IF NOT EXISTS received_at timestamptz DEFAULT now();  -- reported_at can be earlier (sent from an offline queue)
-- Reports too vague to act on wait for a supervisor (backend/report_check.py): no alert until approved.
ALTER TABLE crew_reports ADD COLUMN IF NOT EXISTS review_reason text;
ALTER TABLE crew_reports DROP CONSTRAINT IF EXISTS crew_reports_status_check;
ALTER TABLE crew_reports ADD CONSTRAINT crew_reports_status_check CHECK (status IN ('open', 'resolved', 'dismissed', 'needs_review'));

CREATE TABLE IF NOT EXISTS site_alerts (
    alert_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id         bigint NOT NULL REFERENCES sites,
    category        text NOT NULL REFERENCES hazard_categories,
    level           text NOT NULL CHECK (level IN ('warning', 'stop_work')),
    started_at      timestamptz NOT NULL,
    last_trigger_at timestamptz NOT NULL,
    all_clear_at    timestamptz,                         -- countdown end; moves forward on each new trigger
    cleared_at      timestamptz,                         -- set when the countdown passes or a supervisor clears it
    trigger_value   numeric,                             -- e.g. distance of the strike, heat index
    message         text
);
CREATE INDEX IF NOT EXISTS site_alerts_open_idx ON site_alerts (site_id, category) WHERE cleared_at IS NULL;
ALTER TABLE site_alerts ADD COLUMN IF NOT EXISTS notified_at timestamptz;  -- set when the notifier delivered it
ALTER TABLE site_alerts ADD COLUMN IF NOT EXISTS image_url   text;         -- e.g. wildlife photo

-- Raise or extend an alert. A new trigger on an open alert of the same site/category/level restarts its
-- countdown (when the category says so) instead of opening a second alert. p_until keeps an alert open at
-- least until then (an NWS warning's expiry time).
DROP FUNCTION IF EXISTS raise_site_alert(bigint, text, text, timestamptz, numeric, text);
DROP FUNCTION IF EXISTS raise_site_alert(bigint, text, text, timestamptz, numeric, text, timestamptz);
CREATE OR REPLACE FUNCTION raise_site_alert(p_site bigint, p_category text, p_level text,
                                            p_at timestamptz, p_value numeric DEFAULT NULL, p_message text DEFAULT NULL,
                                            p_until timestamptz DEFAULT NULL, p_image text DEFAULT NULL)
RETURNS site_alerts LANGUAGE plpgsql AS $$
DECLARE
    rule       hazard_categories;
    a          site_alerts;
    new_clear  timestamptz;
BEGIN
    SELECT * INTO STRICT rule FROM hazard_categories WHERE category = p_category;
    new_clear := greatest(p_at + make_interval(mins => rule.all_clear_minutes), p_until);  -- greatest skips NULLs
    UPDATE site_alerts SET cleared_at = all_clear_at              -- close countdowns that already ran out
     WHERE site_id = p_site AND category = p_category AND cleared_at IS NULL AND all_clear_at <= p_at;
    SELECT * INTO a FROM site_alerts
     WHERE site_id = p_site AND category = p_category AND level = p_level AND cleared_at IS NULL
     ORDER BY started_at DESC LIMIT 1 FOR UPDATE;
    IF FOUND THEN
        UPDATE site_alerts
           SET last_trigger_at = greatest(last_trigger_at, p_at),
               all_clear_at = CASE WHEN rule.all_clear_restarts OR p_until IS NOT NULL
                                   THEN greatest(all_clear_at, new_clear) ELSE all_clear_at END,
               trigger_value = coalesce(p_value, trigger_value),
               message = CASE WHEN p_message IS NULL OR position(p_message IN coalesce(message, '')) > 0 THEN message
                              WHEN new_clear >= all_clear_at OR message IS NULL THEN p_message
                              ELSE left(message || ' · also: ' || p_message, 500) END,
               image_url = coalesce(p_image, image_url)
         WHERE alert_id = a.alert_id
     RETURNING * INTO a;
    ELSE
        INSERT INTO site_alerts (site_id, category, level, started_at, last_trigger_at, all_clear_at, trigger_value,
                                 message, image_url)
        VALUES (p_site, p_category, p_level, p_at, p_at, new_clear, p_value, p_message, p_image)
        RETURNING * INTO a;
    END IF;
    RETURN a;
END $$;

DROP VIEW IF EXISTS site_live_floor;
DROP VIEW IF EXISTS active_site_alerts;
CREATE VIEW active_site_alerts AS
SELECT a.*, s.name AS site_name, s.timezone,
       CASE a.level WHEN 'stop_work' THEN 'STOP WORK: ' ELSE 'Warning: ' END || c.label AS title,
       greatest(a.all_clear_at - now(), interval '0') AS all_clear_remaining
  FROM site_alerts a JOIN sites s USING (site_id) JOIN hazard_categories c USING (category)
 WHERE a.cleared_at IS NULL AND (a.all_clear_at IS NULL OR a.all_clear_at > now());

-- Live adjustment of today's Hazard Index: what's happening now sets a floor under the model's forecast score,
-- graded by how close and how intense the hazard is. Stop work -> 8 to 10, warning -> 5 to 7, open crew report -> 4;
-- the highest wins, with its reason. Applied at read time (GET /sites), so it moves the moment an alert changes.
--   proximity (lightning, wildfire, wildlife; trigger_value = miles away): a strike at the site scores 10, one at the
--     10-mile stop-work edge 8; between 10 and 20 mi, 7 down to 5.
--   intensity (heat, cold, wind, fog, rain, soil saturation; trigger_value = the reading): from the warn threshold
--     to the stop threshold scores 5 to 7; past the stop threshold, 8 rising to 10 one band further.
--   NWS warnings and river gauges carry no comparable value and keep the base (8 or 5).
CREATE VIEW site_live_floor AS
SELECT DISTINCT ON (site_id) site_id, floor, reason
  FROM (SELECT a.site_id,
               CASE WHEN a.level = 'warning' AND a.message LIKE 'Crew report:%' AND a.message NOT LIKE '% · also: %' THEN 4
                    WHEN a.level = 'warning' AND a.category = 'flooding'
                         AND (a.message LIKE 'Action flooding%' OR a.message LIKE 'Action stage%')
                         AND a.message NOT LIKE '% · also: %' THEN 3.5
                    ELSE round((CASE a.level WHEN 'stop_work' THEN 8 ELSE 5 END) + 2 * coalesce(g.r, 0), 1) END AS floor,
               a.title || coalesce(': ' || a.message, '') AS reason
          FROM active_site_alerts a JOIN hazard_categories c USING (category)
          CROSS JOIN LATERAL (SELECT least(1, greatest(0, CASE
                   WHEN a.trigger_value IS NULL THEN NULL
                   WHEN a.category IN ('lightning', 'wildfire', 'wildlife') THEN
                        CASE WHEN a.level = 'stop_work' THEN 1 - a.trigger_value / nullif(c.stop_radius_mi, 0)
                             ELSE (c.warn_radius_mi - a.trigger_value) / nullif(c.warn_radius_mi - c.stop_radius_mi, 0) END
                   WHEN a.category = 'flooding' AND a.message NOT LIKE 'Flood risk:%' THEN NULL  -- river stage, not rain
                   ELSE (a.trigger_value - CASE a.level WHEN 'stop_work' THEN c.stop_value ELSE c.warn_value END)
                        / nullif(c.stop_value - c.warn_value, 0) END)) AS r) g
        UNION ALL
        SELECT site_id, 4, 'Open crew report: ' || replace(category, '_', ' ')
          FROM crew_reports
         WHERE status = 'open' AND site_id IS NOT NULL AND reported_at > now() - interval '12 hours') f
 ORDER BY site_id, floor DESC;

CREATE TABLE IF NOT EXISTS hazard_scores (               -- ML hazard index output
    site_id         bigint NOT NULL REFERENCES sites,
    scored_for      timestamptz NOT NULL,                -- the hour/day the score applies to
    horizon         text NOT NULL CHECK (horizon IN ('now', 'next_day')),
    score           numeric(3, 1) NOT NULL CHECK (score BETWEEN 0 AND 10),
    category_scores jsonb,                               -- {"lightning": 7.5, "extreme_heat": 6.0, ...}
    model_version   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, scored_for, horizon)
);

CREATE TABLE IF NOT EXISTS loss_scores (                 -- ml/loss_model.py: predicted outage burden -> est. dollars
    site_id                 bigint NOT NULL REFERENCES sites,
    scored_for              timestamptz NOT NULL,
    horizon                 text NOT NULL CHECK (horizon IN ('now', 'next_day')),
    customer_hours_out_pred numeric NOT NULL,            -- predicted EAGLE-I burden in the site's county (customers x h)
    est_cost_usd            numeric,                     -- x economic_assumptions.usd_per_customer_hour_out (assumption)
    model_version           text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, scored_for, horizon)
);
SELECT create_hypertable('hazard_scores', by_range('scored_for', INTERVAL '1 month'), if_not_exists => true);

CREATE TABLE IF NOT EXISTS daily_briefings (
    site_id         bigint NOT NULL REFERENCES sites,
    briefing_date   date NOT NULL,                       -- the workday the briefing covers
    send_at         timestamptz NOT NULL,                -- 6 PM local the evening before
    sent_at         timestamptz,
    hazard_score    numeric(3, 1),
    summary         text,
    content         jsonb,
    PRIMARY KEY (site_id, briefing_date)
);
ALTER TABLE daily_briefings ADD COLUMN IF NOT EXISTS notified_at timestamptz;

CREATE TABLE IF NOT EXISTS site_reports (                -- on-tap AI report (backend/reports.py), cached 30 min
    report_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id             bigint NOT NULL REFERENCES sites,
    created_at          timestamptz NOT NULL DEFAULT now(),
    generator           text NOT NULL,                   -- gemini:<model> or template
    facts               jsonb NOT NULL,                  -- exactly what the model was given, with source ids
    report              jsonb NOT NULL,
    verified            boolean NOT NULL,                -- false when the model wrote numbers not in the facts
    unverified_numbers  text[],
    errors              text[]                           -- Gemini failures before falling back
);
CREATE INDEX IF NOT EXISTS site_reports_site_idx ON site_reports (site_id, created_at DESC);
ALTER TABLE site_reports ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'safety';  -- safety / compliance

CREATE TABLE IF NOT EXISTS site_score_climatology (      -- each site's normal Hazard Index range by month
    site_id     bigint NOT NULL REFERENCES sites,
    month       int NOT NULL CHECK (month BETWEEN 1 AND 12),
    q           numeric[] NOT NULL,                       -- percentiles 0..100 of the daily index (101 values)
    PRIMARY KEY (site_id, month)
);

-- ===========================================================================================================
-- Site <-> hazard ties: every historical event within its category's history_radius_mi of a site.
-- Rebuilt by refresh_site_hazard_events(), so changing a radius and calling it re-ties every site.
-- Crew-type reports (PPE, machinery, slips, OSHA inspections) count for a site only within 20 mi, because nothing
-- else ties them to the plant, unless they are tied to it: an OSHA report from a power utility or power-line
-- contractor (NAICS 2211xx / 237130) keeps the category's full radius, and so does a crew report filed for the site.
-- ===========================================================================================================
CREATE TABLE IF NOT EXISTS site_hazard_events (
    site_id             bigint NOT NULL REFERENCES sites,
    category            text NOT NULL REFERENCES hazard_categories,
    source              text NOT NULL CHECK (source IN ('noaa_storm_event', 'osha_injury', 'crew_report')),
    source_id           text NOT NULL,                   -- storm_events.event_id / osha_injuries.report_id / crew_reports.report_id
    event_time          timestamptz NOT NULL,
    distance_mi         numeric(6, 2) NOT NULL,
    deaths              int NOT NULL DEFAULT 0,
    injuries            int NOT NULL DEFAULT 0,
    damage_usd          numeric,
    location_precision  text,                            -- point / path / zone
    PRIMARY KEY (site_id, category, source, source_id, event_time)
);
SELECT create_hypertable('site_hazard_events', by_range('event_time', INTERVAL '1 year'), if_not_exists => true);
CREATE INDEX IF NOT EXISTS site_hazard_events_site_idx ON site_hazard_events (site_id, category, event_time DESC);

CREATE OR REPLACE FUNCTION refresh_site_hazard_events() RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    TRUNCATE site_hazard_events;
    INSERT INTO site_hazard_events
    SELECT s.site_id, e.category, 'noaa_storm_event', e.event_id::text, e.begin_time,
           round((ST_Distance(e.geog, s.geog) / 1609.344)::numeric, 2),
           e.deaths_direct + e.deaths_indirect, e.injuries_direct + e.injuries_indirect,
           coalesce(e.damage_property_usd, 0) + coalesce(e.damage_crops_usd, 0), e.location_precision
      FROM hazard_categories c
      JOIN storm_events e ON e.category = c.category
      JOIN sites s ON ST_DWithin(e.geog, s.geog, c.history_radius_mi * 1609.344);
    INSERT INTO site_hazard_events
    SELECT s.site_id, o.category, 'osha_injury', o.report_id::text, o.event_time,
           round((ST_Distance(o.geog, s.geog) / 1609.344)::numeric, 2), 0, 1, NULL, 'point'
      FROM (SELECT r.*, cat AS category     -- crew and weather categories never overlap, so no duplicates
              FROM osha_injuries r,
                   unnest(r.crew_categories || CASE WHEN r.weather_category IS NULL THEN '{}'::text[]
                                                    ELSE ARRAY[r.weather_category] END) AS cat) o
      JOIN hazard_categories c ON c.category = o.category
      JOIN sites s ON ST_DWithin(o.geog, s.geog, 1609.344 * CASE
                WHEN c.kind = 'crew' AND NOT coalesce(o.naics LIKE '2211%' OR o.naics = '237130', false)
                THEN least(c.history_radius_mi, 20) ELSE c.history_radius_mi END);
    INSERT INTO site_hazard_events
    SELECT s.site_id, r.category, 'crew_report', r.report_id::text, r.reported_at,
           round((ST_Distance(r.geog, s.geog) / 1609.344)::numeric, 2), 0, 0, NULL, 'point'
      FROM crew_reports r
      JOIN hazard_categories c ON c.category = r.category
      JOIN sites s ON ST_DWithin(r.geog, s.geog, 1609.344 * CASE
                WHEN c.kind = 'crew' AND r.site_id IS DISTINCT FROM s.site_id
                THEN least(c.history_radius_mi, 20) ELSE c.history_radius_mi END);
    -- compress history right away instead of waiting for the policy job (keeps the free tier's storage low)
    PERFORM compress_chunk(ch, if_not_compressed => true)
       FROM show_chunks('site_hazard_events', older_than => INTERVAL '30 days') ch;
    SELECT count(*) INTO n FROM site_hazard_events;
    RETURN n;
END $$;

ALTER TABLE site_hazard_events SET (timescaledb.enable_columnstore = true,
                                    timescaledb.segmentby = 'site_id, category',
                                    timescaledb.orderby = 'event_time DESC');
CALL add_columnstore_policy('site_hazard_events', after => INTERVAL '30 days', if_not_exists => true);

-- ===========================================================================================================
-- Economic impact: which company runs each site, its sales and demand, crew trips, and lost crew time.
-- Dollar figures depend on economic_assumptions, every one labeled as an assumption until replaced with
-- company data. Correlations are computed in ml/economic_impact.py (never by an LLM).
-- ===========================================================================================================
-- full: the Southeast study area (FL/GA/AL), with history, the Hazard Index model and economics.
-- live: the rest of the lower 48, thinned to 2 plants per 50-mile square (scraper/us_live_sites.py): live alerts,
-- forecasts, readings, lightning and rivers only.
ALTER TABLE sites ADD COLUMN IF NOT EXISTS coverage text NOT NULL DEFAULT 'full' CHECK (coverage IN ('full', 'live'));
ALTER TABLE sites ADD COLUMN IF NOT EXISTS utility_id          bigint;  -- EIA utility (operator) ID
ALTER TABLE sites ADD COLUMN IF NOT EXISTS utility_name        text;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS balancing_authority text;    -- grid operator, e.g. FPL, SOCO

CREATE TABLE IF NOT EXISTS utility_monthly_sales (       -- EIA-861M retail sales, all sectors
    utility_id              bigint NOT NULL,
    utility_name            text,
    state                   char(2) NOT NULL,
    month                   date NOT NULL,
    revenue_thousand_usd    numeric,
    sales_mwh               numeric,
    customers               bigint,
    PRIMARY KEY (utility_id, state, month)
);

CREATE TABLE IF NOT EXISTS ba_daily_demand (             -- EIA-930 daily demand per grid operator
    balancing_authority     text NOT NULL,
    day                     date NOT NULL,
    demand_mwh              numeric,
    PRIMARY KEY (balancing_authority, day)
);

CREATE TABLE IF NOT EXISTS crew_trips (                  -- real trip logs from a utility (no public source exists)
    trip_id         text PRIMARY KEY,
    site_id         bigint REFERENCES sites,
    started_at      timestamptz NOT NULL,
    ended_at        timestamptz,
    crew_size       int,
    purpose         text,                                -- e.g. maintenance, storm restoration
    cost_usd        numeric,
    geog            geography(Point, 4326),
    source          text NOT NULL DEFAULT 'company'
);
CREATE INDEX IF NOT EXISTS crew_trips_site_idx ON crew_trips (site_id, started_at);

CREATE TABLE IF NOT EXISTS economic_assumptions (
    key     text PRIMARY KEY,
    value   numeric NOT NULL,
    unit    text,
    note    text
);
INSERT INTO economic_assumptions VALUES
    ('crew_size', 4, 'people', 'ASSUMPTION: typical field crew; replace with company data'),
    ('shift_hours', 10, 'hours', 'ASSUMPTION: one shift; caps lost hours per site-day'),
    ('loaded_cost_per_hour', 85, 'USD per person-hour', 'ASSUMPTION: wage plus overhead; replace with company data'),
    ('hours_lost_lightning', 2, 'hours per stop-work day', 'ASSUMPTION: 30-min all-clears that keep restarting'),
    ('hours_lost_severe_storm', 3, 'hours per stop-work day', 'ASSUMPTION'),
    ('hours_lost_tropical', 10, 'hours per stop-work day', 'ASSUMPTION: full shift'),
    ('hours_lost_flooding', 6, 'hours per stop-work day', 'ASSUMPTION'),
    ('hours_lost_extreme_heat', 3, 'hours per stop-work day', 'ASSUMPTION: afternoon work moved or stopped'),
    ('hours_lost_extreme_cold', 2, 'hours per stop-work day', 'ASSUMPTION'),
    ('hours_lost_high_wind', 2, 'hours per stop-work day', 'ASSUMPTION: aerial and crane work paused'),
    ('hours_lost_soil_saturation', 4, 'hours per stop-work day', 'ASSUMPTION: equipment stuck, access delays'),
    ('hours_lost_wildfire', 10, 'hours per stop-work day', 'ASSUMPTION'),
    ('hours_lost_dense_fog', 2, 'hours per stop-work day', 'ASSUMPTION'),
    ('usd_per_customer_hour_out', 5, 'USD per customer-hour without power',
     'ASSUMPTION: rough value of lost load / restoration cost per customer-hour; replace with company data')
ON CONFLICT (key) DO NOTHING;  -- keep the team's edits

CREATE TABLE IF NOT EXISTS county_outages_daily (       -- EAGLE-I (ORNL): stand-in for restoration crew workload
    county_fips         char(5) NOT NULL,
    day                 date NOT NULL,                   -- Eastern date
    customer_hours_out  numeric NOT NULL,                -- sum over 15-min snapshots of customers out x 0.25 h
    peak_customers_out  int,
    customers           int,                             -- customers tracked in the county
    PRIMARY KEY (county_fips, day)
);

CREATE TABLE IF NOT EXISTS economic_weekly (             -- per grid operator per week (Monday start)
    balancing_authority     text NOT NULL,
    week                    date NOT NULL,
    sites                   int,
    demand_mwh              numeric,                     -- EIA-930, full weeks only
    stop_work_site_days     int,                         -- weather days over a stop threshold, summed over sites
    warning_site_days       int,
    storm_events            int,                         -- NOAA events tied to the operator's sites
    crew_hours_lost         numeric,                     -- stop-work site-days x hours_lost_<category> x crew_size
    est_cost_usd            numeric,                     -- crew_hours_lost x loaded_cost_per_hour (assumption)
    crew_trips              int,                         -- from crew_trips, when real trip logs are loaded
    PRIMARY KEY (balancing_authority, week)
);
ALTER TABLE economic_weekly ADD COLUMN IF NOT EXISTS outage_customer_hours numeric;  -- EAGLE-I, the operator's site counties
ALTER TABLE economic_weekly ADD COLUMN IF NOT EXISTS avg_price_usd_per_mwh numeric;  -- EIA-861M revenue / MWh that month
ALTER TABLE economic_weekly ADD COLUMN IF NOT EXISTS est_revenue_usd       numeric;  -- demand x average price (estimate)

-- Contract disruption (ml/contract_disruption.py). Public stand-ins until a utility's own contract records exist:
-- federal field-construction contracts (USAspending), company revenue (SEC), storm damage (OpenFEMA).
CREATE TABLE IF NOT EXISTS field_contracts (
    award_key           text PRIMARY KEY,                -- USAspending contract_award_unique_key
    piid                text,
    recipient           text,
    recipient_parent    text,
    recipient_uei       text,
    agency              text,
    sub_agency          text,
    naics               text,
    naics_description   text,
    description         text,
    county_fips         text NOT NULL,                   -- where the work was performed
    county_name         text,
    state               text,
    city                text,
    start_date          date,
    original_end_date   date,                            -- as first awarded
    final_end_date      date,                            -- after every change
    last_action_date    date,
    obligated_usd       numeric,
    potential_value_usd numeric,
    mods                int,
    change_orders       int,
    terminated          text,                            -- default / cause / convenience / cancelled / ''
    slip_days           int,                             -- end date moved later by changes other than option years
    weather_cited       boolean,                         -- a change says weather held the work up
    weather_cited_text  text
);
CREATE INDEX IF NOT EXISTS field_contracts_county_idx ON field_contracts (county_fips);
CREATE TABLE IF NOT EXISTS contract_weather_attribution (
    award_key           text PRIMARY KEY REFERENCES field_contracts ON DELETE CASCADE,
    model_version       text NOT NULL,
    disrupted           boolean NOT NULL,                -- terminated, or slipped 30+ days
    exposure            jsonb NOT NULL,                  -- weather days by type in the county during the planned window
    p_disruption        numeric,                         -- model, with the weather that happened
    p_without_weather   numeric,                         -- model, same contract with no disruptive weather
    prob_weather_caused numeric,                         -- 1 - p_without / p_with (0 when weather didn't raise it)
    attributed          boolean NOT NULL,                -- disrupted and prob_weather_caused >= 0.5
    top_weather         text,
    site_ids            bigint[],                        -- power plant sites in the same county
    evidence            text,                            -- documented_and_corroborated / documented / storm_overlap_only / none
    storm_match         text                             -- NOAA storm days that corroborate the documented delay
);
CREATE TABLE IF NOT EXISTS company_quarterly_revenue (    -- SEC EDGAR XBRL
    cik                 bigint NOT NULL,
    company             text NOT NULL,
    kind                text NOT NULL CHECK (kind IN ('contractor', 'utility')),
    quarter_end         date NOT NULL,
    revenue_usd         numeric NOT NULL,
    tag                 text,
    PRIMARY KEY (cik, quarter_end)
);
CREATE TABLE IF NOT EXISTS fema_declarations (
    disaster_number     int NOT NULL,
    county_fips         text NOT NULL,
    incident_type       text,
    title               text,
    incident_begin      date,
    incident_end        date,
    declared            date,
    PRIMARY KEY (disaster_number, county_fips)
);
CREATE TABLE IF NOT EXISTS fema_utility_projects (       -- Public Assistance, damage category F (utilities)
    disaster_number     int NOT NULL,
    pw_number           int NOT NULL,
    county_fips         text,
    incident_type       text,
    title               text,
    declared            date,
    project_usd         numeric,
    PRIMARY KEY (disaster_number, pw_number)
);

-- NOAA Tides & Currents water-level stations near the sites (backend/tides.py). Levels are in station datum (STND),
-- the same datum NOAA publishes its flood levels in, so a reading compares directly with minor/moderate/major.
CREATE TABLE IF NOT EXISTS tide_stations (
    station_id      text PRIMARY KEY,
    name            text,
    state           text,
    geog            geography(Point, 4326) NOT NULL,
    mhhw_ft         numeric,                             -- mean higher high water (a normal high tide)
    minor_ft        numeric,                             -- NOAA (NOS) flood levels
    moderate_ft     numeric,
    major_ft        numeric,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tide_readings (
    station_id      text NOT NULL REFERENCES tide_stations ON DELETE CASCADE,
    observed_at     timestamptz NOT NULL,
    level_ft        numeric NOT NULL,
    PRIMARY KEY (station_id, observed_at)
);
INSERT INTO hazard_categories (category, label, kind, metric, unit, warn_radius_mi, stop_radius_mi, history_radius_mi,
                               window_minutes, notes)
VALUES ('tidal_flooding', 'Coastal (tidal) flooding', 'environmental', 'water_level_ft', 'ft', 10, 10, 10, 60,
        'A NOAA Tides & Currents station within 10 mi at or above its minor flood level: warning; moderate: stop work.')
ON CONFLICT (category) DO NOTHING;

-- USACE National Levee Database near the sites (scraper/levees.py, db/load_levees.py).
CREATE TABLE IF NOT EXISTS levee_features (
    feature_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind            text NOT NULL CHECK (kind IN ('leveed_area', 'levee_line', 'borehole')),
    system_id       text,
    system_name     text,
    props           jsonb NOT NULL DEFAULT '{}',
    geog            geography NOT NULL
);
CREATE INDEX IF NOT EXISTS levee_features_geog_idx ON levee_features USING gist (geog);
CREATE OR REPLACE VIEW site_levees AS                 -- per site: behind a levee? how good is it? how close is one?
SELECT s.site_id, la.system_name AS leveed_area_system,
       la.props ->> 'FEMA_ACCREDITATION_RATING' AS levee_accreditation,
       (la.props ->> 'OVERTOPPING_AEP') AS overtopping_chance_per_year,
       (la.props ->> 'PEOPLE') AS people_behind_levee, la.props ->> 'PRIMARY_FLOOD_SOURCE' AS levee_flood_source,
       ll.system_name AS nearest_levee, round(ll.miles::numeric, 1) AS nearest_levee_mi, bh.n AS soil_borings_5mi
  FROM sites s
  LEFT JOIN LATERAL (SELECT system_name, props FROM levee_features f
                      WHERE f.kind = 'leveed_area' AND ST_Covers(f.geog, s.geog) LIMIT 1) la ON true
  LEFT JOIN LATERAL (SELECT system_name, ST_Distance(f.geog, s.geog) / 1609.344 AS miles FROM levee_features f
                      WHERE f.kind = 'levee_line' AND ST_DWithin(f.geog, s.geog, 16093)
                      ORDER BY f.geog <-> s.geog LIMIT 1) ll ON true
  LEFT JOIN LATERAL (SELECT count(*) AS n FROM levee_features f
                      WHERE f.kind = 'borehole' AND ST_DWithin(f.geog, s.geog, 8047)) bh ON true;

-- Investment planning (backend/plan.py): a fix per hazard with its cost and how much of the lost crew time it
-- avoids. All labeled ASSUMPTION until a utility's real project costs replace them (edit these rows).
INSERT INTO economic_assumptions (key, value, unit, note) VALUES
('mitigation_cost_lightning', 45000, 'USD', 'ASSUMPTION: site lightning-prediction system + hardened crew shelter'),
('mitigation_reduction_lightning', 0.25, 'share of lost time avoided', 'ASSUMPTION: earlier warning, shorter stand-downs'),
('mitigation_cost_extreme_heat', 25000, 'USD', 'ASSUMPTION: permanent shade structures + cooling station'),
('mitigation_reduction_extreme_heat', 0.40, 'share of lost time avoided', 'ASSUMPTION: shade/cooling plus early shifts'),
('mitigation_cost_flooding', 120000, 'USD', 'ASSUMPTION: drainage, elevated equipment pad, gravel access road'),
('mitigation_reduction_flooding', 0.50, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_tidal_flooding', 150000, 'USD', 'ASSUMPTION: elevated pads and removable flood barriers'),
('mitigation_reduction_tidal_flooding', 0.50, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_soil_saturation', 60000, 'USD', 'ASSUMPTION: crane mats + stabilized gravel access'),
('mitigation_reduction_soil_saturation', 0.50, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_severe_storm', 40000, 'USD', 'ASSUMPTION: storm shelter + equipment tie-down points'),
('mitigation_reduction_severe_storm', 0.25, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_tropical', 75000, 'USD', 'ASSUMPTION: hurricane prep kit + secured laydown yard'),
('mitigation_reduction_tropical', 0.20, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_high_wind', 10000, 'USD', 'ASSUMPTION: on-site anemometer + wind-rated lift plan'),
('mitigation_reduction_high_wind', 0.30, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_extreme_cold', 15000, 'USD', 'ASSUMPTION: heated warm-up shelter'),
('mitigation_reduction_extreme_cold', 0.30, 'share of lost time avoided', 'ASSUMPTION'),
('mitigation_cost_dense_fog', 8000, 'USD', 'ASSUMPTION: site lighting + spotter program'),
('mitigation_reduction_dense_fog', 0.30, 'share of lost time avoided', 'ASSUMPTION')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS weather_bounceback (          -- ml/bounceback.py: does business rise after a storm?
    scope           text NOT NULL,                       -- county_field_contracts / national_contractor_revenue / ...
    weather_kind    text NOT NULL,
    outcome         text NOT NULL,
    horizon         text NOT NULL,                       -- e.g. months +1..+6
    effect          numeric,                             -- bounce vs the months before (outcome units)
    ci_low          numeric,                             -- 95% interval
    ci_high         numeric,
    relative        numeric,                             -- effect as a share of normal (0.25 = +25%)
    events          int,
    supported       boolean NOT NULL,                    -- interval entirely above zero
    model_version   text,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, weather_kind, outcome)
);

-- Each worker job's last run (backend/worker.py), so GET /data-status can tell the dashboard whether live data is
-- current. A job that hasn't succeeded within a few of its intervals is "stale"; one whose last run failed is "failing".
CREATE TABLE IF NOT EXISTS worker_runs (
    job             text PRIMARY KEY,
    last_run_at     timestamptz NOT NULL,
    last_success_at timestamptz,
    last_error      text,
    detail          text
);

-- Real crew dispatches from utilities' public outage maps (backend/crew_dispatch.py, worker job "crew_dispatch").
-- One row per outage near a site, with when the utility marked a crew assigned / on site and when power came back.
CREATE TABLE IF NOT EXISTS crew_dispatch (
    incident_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    utility         text NOT NULL,
    geog            geography(Point, 4326) NOT NULL,
    site_id         bigint REFERENCES sites,             -- nearest site (within 25 mi)
    site_miles      numeric,
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    assigned_at     timestamptz,                         -- first poll whose status said a crew was assigned
    on_site_at      timestamptz,                         -- first poll whose status said the crew was on site
    cleared_at      timestamptz,                         -- gone from the map (restored)
    customers_max   int,
    cause           text,
    last_comment    text
);
CREATE INDEX IF NOT EXISTS crew_dispatch_open_idx ON crew_dispatch (utility) WHERE cleared_at IS NULL;
CREATE INDEX IF NOT EXISTS crew_dispatch_geog_idx ON crew_dispatch USING gist (geog);
CREATE INDEX IF NOT EXISTS crew_dispatch_site_idx ON crew_dispatch (site_id, first_seen);

-- Weather measured in real sales (ml/weather_sales.py): degree-day model of EIA-861M monthly sales per utility.
CREATE TABLE IF NOT EXISTS weather_sales_model (
    utility_id          bigint NOT NULL,
    state               char(2) NOT NULL,
    utility_name        text,
    months              int,
    r2                  numeric,                         -- sales ~ trend + days + CDD + HDD
    r2_without_weather  numeric,                         -- sales ~ trend + days
    mwh_per_cdd         numeric,
    mwh_per_hdd         numeric,
    first_month         date,
    last_month          date,
    PRIMARY KEY (utility_id, state)
);
CREATE TABLE IF NOT EXISTS weather_sales_effect (
    utility_id          bigint NOT NULL,
    state               char(2) NOT NULL,
    utility_name        text,
    month               date NOT NULL,
    sales_mwh           numeric,                         -- real (EIA-861M)
    cdd                 numeric,
    hdd                 numeric,
    weather_mwh         numeric,                         -- sales above (+) or below (-) a normal-weather month
    weather_revenue_usd numeric,                         -- weather_mwh x that month's average retail price
    PRIMARY KEY (utility_id, state, month)
);

CREATE TABLE IF NOT EXISTS economic_correlations (
    scope           text NOT NULL,                       -- weekly_ba / monthly_utility
    entity          text NOT NULL,                       -- grid operator code or utility name
    metric_x        text NOT NULL,                       -- weather / crew metric
    metric_y        text NOT NULL,                       -- demand or sales metric
    deseasonalized  boolean NOT NULL,                    -- true: both series minus their same-month average
    r               numeric,                             -- Pearson correlation
    p_value         numeric,
    spearman        numeric,
    n               int,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, entity, metric_x, metric_y, deseasonalized)
);

-- ===========================================================================================================
-- Compression and retention (columnstore) for the high-volume live feeds
-- ===========================================================================================================
ALTER TABLE lightning_strikes SET (timescaledb.enable_columnstore = true, timescaledb.orderby = 'observed_at DESC');
CALL add_columnstore_policy('lightning_strikes', after => INTERVAL '2 days', if_not_exists => true);
SELECT add_retention_policy('lightning_strikes', INTERVAL '90 days', if_not_exists => true);

ALTER TABLE weather_observations SET (timescaledb.enable_columnstore = true, timescaledb.segmentby = 'site_id');
CALL add_columnstore_policy('weather_observations', after => INTERVAL '14 days', if_not_exists => true);

ALTER TABLE river_gauge_readings SET (timescaledb.enable_columnstore = true, timescaledb.segmentby = 'gauge_id');
CALL add_columnstore_policy('river_gauge_readings', after => INTERVAL '14 days', if_not_exists => true);

-- ===========================================================================================================
-- Per-site risk profile: one row per site, one set of columns per category. Every count uses that
-- category's history_radius_mi (and thresholds) from hazard_categories, so retuning a range changes the
-- profile on the next REFRESH MATERIALIZED VIEW site_risk_profile.
-- ===========================================================================================================
DROP MATERIALIZED VIEW IF EXISTS site_risk_profile;
DROP VIEW IF EXISTS site_category_history;
DROP VIEW IF EXISTS site_weather_days;

-- Daily weather ties: each day the site's station crossed a category's warn/stop threshold.
-- Weather is per station, so this joins through sites.weather_station_id instead of storing a copy per site.
CREATE VIEW site_weather_days AS
WITH d AS (
    SELECT w.*, sum(w.precip_in) OVER (PARTITION BY w.station_id ORDER BY w.day
                                       RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW) AS rain_7d_in
      FROM weather_daily w
), v AS (
    SELECT station_id, day, 'extreme_heat'::text AS category, max_heat_index_f AS value FROM d
    UNION ALL SELECT station_id, day, 'extreme_cold', min_temp_f FROM d
    UNION ALL SELECT station_id, day, 'flooding', precip_in FROM d
    UNION ALL SELECT station_id, day, 'high_wind', round(max_wind_kt * 1.15078, 1) FROM d    -- sustained, knots -> mph
    UNION ALL SELECT station_id, day, 'soil_saturation', rain_7d_in FROM d
)
SELECT s.site_id, v.category, v.day, v.value, c.unit,
       CASE WHEN (NOT c.lower_is_worse AND v.value >= c.stop_value) OR (c.lower_is_worse AND v.value <= c.stop_value)
            THEN 'stop_work' ELSE 'warning' END AS level,
       s.weather_station_id AS station_id, s.weather_station_mi AS station_distance_mi
  FROM v
  JOIN hazard_categories c ON c.category = v.category
  JOIN sites s ON s.weather_station_id = v.station_id
 WHERE (NOT c.lower_is_worse AND v.value >= c.warn_value) OR (c.lower_is_worse AND v.value <= c.warn_value);

CREATE VIEW site_category_history AS        -- long form: site x category, from the stored ties
WITH ev AS (
    SELECT site_id, category,
           count(*) FILTER (WHERE source = 'noaa_storm_event')                    AS storm_events,
           coalesce(sum(deaths)     FILTER (WHERE source = 'noaa_storm_event'), 0) AS storm_deaths,
           coalesce(sum(injuries)   FILTER (WHERE source = 'noaa_storm_event'), 0) AS storm_injuries,
           coalesce(sum(damage_usd) FILTER (WHERE source = 'noaa_storm_event'), 0) AS storm_damage_usd,
           max(event_time)          FILTER (WHERE source = 'noaa_storm_event')    AS last_storm_event,
           count(*) FILTER (WHERE source = 'osha_injury')                         AS osha_reports,
           count(*) FILTER (WHERE source = 'crew_report')                         AS crew_reports
      FROM site_hazard_events GROUP BY site_id, category
), fatal AS (
    SELECT h.site_id, h.category,
           count(*)                                AS storm_fatalities,
           count(*) FILTER (WHERE f.crew_relevant) AS storm_fatalities_crew_relevant
      FROM site_hazard_events h
      JOIN storm_fatalities f ON f.event_id = h.source_id::bigint
     WHERE h.source = 'noaa_storm_event'
     GROUP BY h.site_id, h.category
)
SELECT s.site_id, c.category, c.history_radius_mi,
       coalesce(ev.storm_events, 0)                       AS storm_events,
       coalesce(ev.storm_deaths, 0)                       AS storm_deaths,
       coalesce(ev.storm_injuries, 0)                     AS storm_injuries,
       coalesce(ev.storm_damage_usd, 0)                   AS storm_damage_usd,
       ev.last_storm_event,
       coalesce(fatal.storm_fatalities, 0)                AS storm_fatalities,
       coalesce(fatal.storm_fatalities_crew_relevant, 0)  AS storm_fatalities_crew_relevant,
       coalesce(ev.osha_reports, 0)                       AS osha_reports,
       coalesce(ev.crew_reports, 0)                       AS crew_reports
  FROM sites s CROSS JOIN hazard_categories c
  LEFT JOIN ev    USING (site_id, category)
  LEFT JOIN fatal USING (site_id, category);

CREATE MATERIALIZED VIEW site_risk_profile AS
WITH h AS (SELECT * FROM site_category_history),
wx AS (                                     -- days per year over each category's thresholds at the site's station
    SELECT s.site_id,
           count(*) FILTER (WHERE d.max_heat_index_f IS NOT NULL)                         AS heat_days_n,
           count(*) FILTER (WHERE d.max_heat_index_f >= heat.warn_value)                  AS heat_warn_days,
           count(*) FILTER (WHERE d.max_heat_index_f >= heat.stop_value)                  AS heat_stop_days,
           count(*) FILTER (WHERE d.precip_in IS NOT NULL)                                AS rain_days_n,
           count(*) FILTER (WHERE d.precip_in >= flood.warn_value)                        AS rain_warn_days,
           count(*)                                                                       AS wind_days_n,
           count(*) FILTER (WHERE d.max_gust_kt * 1.15078 >= wind.stop_value)             AS gust_stop_days,
           count(*) FILTER (WHERE d.min_temp_f <= cold.warn_value)                        AS freeze_days
      FROM sites s
      JOIN weather_daily d ON d.station_id = s.weather_station_id
      CROSS JOIN (SELECT * FROM hazard_categories WHERE category = 'extreme_heat') heat
      CROSS JOIN (SELECT * FROM hazard_categories WHERE category = 'flooding') flood
      CROSS JOIN (SELECT * FROM hazard_categories WHERE category = 'high_wind') wind
      CROSS JOIN (SELECT * FROM hazard_categories WHERE category = 'extreme_cold') cold
     GROUP BY s.site_id
), cdc AS (
    SELECT county_fips, sum(deaths) AS deaths, bool_or(suppressed) AS suppressed
      FROM weather_fatalities GROUP BY county_fips
)
SELECT s.site_id, s.name, s.state, s.county, s.timezone, s.geog,
    -- lightning (history radius from hazard_categories.lightning)
    max(h.storm_events)   FILTER (WHERE h.category = 'lightning')        AS lightning_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'lightning')        AS lightning_deaths,
    max(h.storm_injuries) FILTER (WHERE h.category = 'lightning')        AS lightning_injuries,
    max(h.osha_reports)   FILTER (WHERE h.category = 'lightning')        AS lightning_osha_reports,
    -- extreme heat
    max(h.storm_events)   FILTER (WHERE h.category = 'extreme_heat')     AS heat_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'extreme_heat')     AS heat_deaths,
    max(h.osha_reports)   FILTER (WHERE h.category = 'extreme_heat')     AS heat_osha_reports,
    round(wx.heat_warn_days * 365.25 / nullif(wx.heat_days_n, 0), 1)     AS heat_warn_days_per_year,
    round(wx.heat_stop_days * 365.25 / nullif(wx.heat_days_n, 0), 1)     AS heat_stop_days_per_year,
    -- extreme cold
    max(h.storm_events)   FILTER (WHERE h.category = 'extreme_cold')     AS cold_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'extreme_cold')     AS cold_deaths,
    max(h.osha_reports)   FILTER (WHERE h.category = 'extreme_cold')     AS cold_osha_reports,
    round(wx.freeze_days * 365.25 / nullif(wx.wind_days_n, 0), 1)        AS freeze_days_per_year,
    -- flooding
    max(h.storm_events)   FILTER (WHERE h.category = 'flooding')         AS flood_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'flooding')         AS flood_deaths,
    max(h.storm_damage_usd) FILTER (WHERE h.category = 'flooding')       AS flood_damage_usd,
    round(wx.rain_warn_days * 365.25 / nullif(wx.rain_days_n, 0), 1)     AS heavy_rain_days_per_year,
    -- high wind
    max(h.storm_events)   FILTER (WHERE h.category = 'high_wind')        AS wind_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'high_wind')        AS wind_deaths,
    round(wx.gust_stop_days * 365.25 / nullif(wx.wind_days_n, 0), 1)     AS wind_stop_days_per_year,
    -- severe storms / tornadoes
    max(h.storm_events)   FILTER (WHERE h.category = 'severe_storm')     AS severe_storm_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'severe_storm')     AS severe_storm_deaths,
    max(h.storm_injuries) FILTER (WHERE h.category = 'severe_storm')     AS severe_storm_injuries,
    max(h.storm_damage_usd) FILTER (WHERE h.category = 'severe_storm')   AS severe_storm_damage_usd,
    -- tropical
    max(h.storm_events)   FILTER (WHERE h.category = 'tropical')         AS tropical_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'tropical')         AS tropical_deaths,
    max(h.storm_damage_usd) FILTER (WHERE h.category = 'tropical')       AS tropical_damage_usd,
    -- wildfire, fog, coastal
    max(h.storm_events)   FILTER (WHERE h.category = 'wildfire')         AS wildfire_events,
    max(h.storm_events)   FILTER (WHERE h.category = 'dense_fog')        AS fog_events,
    max(h.storm_deaths)   FILTER (WHERE h.category = 'coastal')          AS coastal_deaths,
    -- wildlife
    max(h.crew_reports)   FILTER (WHERE h.category = 'wildlife')         AS wildlife_reports,
    -- storm fatalities (all categories) and crew-relevant ones (outdoors, heavy equipment, vehicles, under trees)
    sum(h.storm_fatalities)                                              AS storm_fatalities,
    sum(h.storm_fatalities_crew_relevant)                                AS storm_fatalities_crew_relevant,
    -- weather fatalities (CDC, county level; NULL deaths when suppressed)
    cdc.deaths                                                           AS cdc_county_weather_deaths,
    coalesce(cdc.suppressed, false)                                      AS cdc_county_suppressed,
    -- crew hazards (OSHA history + crew reports)
    max(h.osha_reports)   FILTER (WHERE h.category = 'ppe')              AS ppe_osha_reports,
    max(h.crew_reports)   FILTER (WHERE h.category = 'ppe')              AS ppe_crew_reports,
    max(h.osha_reports)   FILTER (WHERE h.category = 'heavy_machinery')  AS machinery_osha_reports,
    max(h.crew_reports)   FILTER (WHERE h.category = 'heavy_machinery')  AS machinery_crew_reports,
    max(h.osha_reports)   FILTER (WHERE h.category = 'slip_trip')        AS slip_trip_osha_reports,
    max(h.crew_reports)   FILTER (WHERE h.category = 'slip_trip')        AS slip_trip_crew_reports,
    max(h.osha_reports)   FILTER (WHERE h.category = 'osha_inspection')  AS osha_inspections,
    now()                                                                AS refreshed_at
  FROM sites s
  LEFT JOIN h   ON h.site_id = s.site_id
  LEFT JOIN wx  ON wx.site_id = s.site_id
  LEFT JOIN cdc ON cdc.county_fips = s.county_fips
 GROUP BY s.site_id, wx.heat_days_n, wx.heat_warn_days, wx.heat_stop_days, wx.rain_days_n, wx.rain_warn_days,
          wx.wind_days_n, wx.gust_stop_days, wx.freeze_days, cdc.deaths, cdc.suppressed;
CREATE UNIQUE INDEX site_risk_profile_site_idx ON site_risk_profile (site_id);
