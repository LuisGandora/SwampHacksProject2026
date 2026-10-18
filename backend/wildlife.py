"""Wildlife sightings near sites from iNaturalist (api.inaturalist.org, no key), with photos.

Sightings from the configured window (default three days) within its warn radius of a site raise a
warning carrying the photo. Crew-reported wildlife goes through POST /crew-reports instead.
"""
import datetime as dt
import os

import httpx

API = "https://api.inaturalist.org/v1/observations"
# Species that matter to field crews in FL/GA/AL; override with WILDLIFE_TAXA="Name;Name;..."
TAXA = os.environ.get("WILDLIFE_TAXA", "Alligator mississippiensis;Agkistrodon piscivorus;Crotalus adamanteus").split(";")
client = httpx.Client(timeout=60, headers={"User-Agent": "PlanIT-Control/0.1 (SwampHacks 2026)"})


def usable_location(o):
    """Only wild, public, reasonably precise records can support proximity alerts.

    100 m is an application quality threshold, not a claim about animal movement.
    Unknown accuracy is excluded rather than presented as an exact site distance.
    """
    accuracy = o.get("positional_accuracy")
    return (not o.get("captive") and not o.get("obscured")
            and o.get("geoprivacy") in (None, "open")
            and o.get("taxon_geoprivacy") in (None, "open")
            and o.get("quality_grade") in ("research", "needs_id")
            and isinstance(accuracy, (int, float)) and 0 <= accuracy <= 100
            and (o.get("geojson") or {}).get("type") == "Point")

RAISE = """
WITH c AS (SELECT * FROM hazard_categories WHERE category = 'wildlife'),
near AS (
    SELECT DISTINCT ON (s.site_id) s.site_id, w.*, ST_Distance(w.geog, s.geog) / 1609.344 AS miles, c.window_minutes
      FROM wildlife_sightings w CROSS JOIN c
      JOIN sites s ON ST_DWithin(w.geog, s.geog, c.warn_radius_mi * 1609.344)
     WHERE w.observed_at > now() - make_interval(mins => c.window_minutes) AND w.source = 'inaturalist'
       AND w.source_ref = ANY (%(eligible)s)
     ORDER BY s.site_id, w.observed_at DESC
)
SELECT count(raise_site_alert(site_id, 'wildlife', 'warning', observed_at, round(miles::numeric, 2),
                              coalesce(common_name, species) || ' seen ' || round(miles::numeric, 1) || ' mi away on '
                                  || to_char(observed_at, 'Mon DD') || ' (iNaturalist)',
                              observed_at + make_interval(mins => window_minutes), photo_url))
  FROM near
"""


def sync(conn):
    box = conn.execute("SELECT min(ST_Y(geog::geometry)) - 0.2 AS s, min(ST_X(geog::geometry)) - 0.2 AS w, "
                       "max(ST_Y(geog::geometry)) + 0.2 AS n, max(ST_X(geog::geometry)) + 0.2 AS e FROM sites").fetchone()
    days = conn.execute("SELECT window_minutes / 1440 AS d FROM hazard_categories WHERE category = 'wildlife'"
                        ).fetchone()["d"] or 7
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = []
    for taxon in TAXA:
        page = 1
        while True:
            r = client.get(API, params={"taxon_name": taxon, "swlat": box["s"], "swlng": box["w"], "nelat": box["n"],
                                        "nelng": box["e"], "d1": since, "per_page": 200, "page": page,
                                        "captive": "false", "quality_grade": "research,needs_id"})
            r.raise_for_status()
            data = r.json()
            for o in data["results"]:
                if not usable_location(o):
                    continue
                lon, lat = o["geojson"]["coordinates"]
                when = o.get("time_observed_at") or o.get("observed_on")
                if not when or not o.get("taxon"):
                    continue
                photo = (o.get("photos") or [{}])[0].get("url")
                rows.append((dt.datetime.fromisoformat(when) if "T" in when else
                             dt.datetime.fromisoformat(when + "T12:00:00+00:00"),
                             f"SRID=4326;POINT({lon} {lat})", o["taxon"]["name"], o["taxon"].get("preferred_common_name"),
                             "inaturalist", str(o["id"]), photo.replace("/square.", "/medium.") if photo else None))
            if page * 200 >= data["total_results"]:
                break
            page += 1
    conn.cursor().executemany(
        "INSERT INTO wildlife_sightings (observed_at, geog, species, common_name, source, source_ref, photo_url) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", rows)
    raised = conn.execute(RAISE, {"eligible": list({r[5] for r in rows})}).fetchone()['count']
    return f"{len(rows)} sightings in the last {days} days, {raised} site alerts raised"
