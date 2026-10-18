# Dashboard feedback follow-up

The daily model, live alert adjustment, and expected crew impact measure different things. The site card now
names the selected day, explains when alerts set today's floor, and labels crew impact as a model estimate
that excludes live adjustments. Small nonzero probabilities/hours display as `<0.1%` / `<0.1`, rather than zero.
Safety reports describe current alerts separately from tomorrow's score. Open crew reports are explicitly
unverified; resolved/dismissed reports are excluded from the report facts. Existing safety-report caches from
the earlier facts format are regenerated on the next request.

## Alert corrections

- Cancellation messages are applied after all NWS inserts, so input order cannot revive a cancelled warning.
- Rip-current, beach-hazard and lake-wind advisories do not raise generic land-crew site alerts. They remain NWS
  map information. The sites table has no waterfront work/exposure field, so these products cannot be reliably
  promoted into site-specific work instructions. Other alerts still use NWS polygons or official forecast zones.
- An action-stage river calls for preparation and does not confirm site flooding. Its warning floor is 3.5.
  Observed minor/moderate/major flooding inside the configured stop radius remains stop-work; outside it, the
  message preserves the flood category and distance while warning of a nearby gauge condition.
- Removed the heuristic that declared a high MHHW gauge reading faulty solely because no tropical/coastal
  warning was stored. That is insufficient evidence of sensor failure. Gauge observations remain labeled as
  observations, not confirmed flooding at a plant.
- iNaturalist ingestion requires wild, non-obscured, public point locations of research/needs-ID quality with
  known positional accuracy at most 100 m. That is an application quality threshold. Only records accepted in
  the current fetch can raise alerts, preventing previously cached rejected records from being renewed.
- Wildlife uses a three-day relevance window; expiration does not imply an animal has left. Crew submissions
  retain their separate 12-hour window. Vague entries are labeled for review rather than silently deleted.
- Alert times include date/timezone and say “expires; may be extended.” A lightning hold does not turn the
  stop-work banner green just because the local timer reaches zero. Only lightning extensions show “new strike.”
- The alerts panel, counts, stop-work banner and desktop notifications follow the selected geographic area.

These interpretations follow the [NWS flood-stage definitions](https://www.weather.gov/lot/hydrology_definitions),
[NWS lake-wind advisory examples](https://www.weather.gov/media/notification/ref/All_Advisory_Examples.pdf), and
[iNaturalist location/quality fields](https://www.inaturalist.org/pages/api%2Breference).

## Existing database upgrade

Use `python -m db.upgrade_alert_consistency --check` for a rolled-back validation, then
`python -m db.upgrade_alert_consistency` to apply atomically. It updates legacy defaults and stored NWS
classifications, clears obsolete advisory/cancellation alerts, updates action-stage wording, and shortens old
wildlife expiry windows. It preserves crew-report records and does not run notification delivery.

Restart the API/worker after deploying Python changes and rebuild the frontend. The migration is safe to repeat.

## Regression checks

```text
python -m backend.test_backend
python -m unittest backend.test_alert_consistency
python -m db.test_alert_consistency
cd frontend
node --test src/hazard.test.js src/freshness.test.js src/components/StopWorkBanner.test.js
npm run build
```

The database test uses a rollback transaction and temporary river fixtures. It checks the real upgrade SQL,
live floors, report-fact/cache queries, and river alerts without leaving test data behind.
