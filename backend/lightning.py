"""Lightning from the GOES-19 Geostationary Lightning Mapper (GLM), free on AWS (bucket noaa-goes19).

A new file lands about every 20 s with the flashes in that window. GLM locates flashes to roughly 8 km,
which is coarse but workable for a 10-mile stop-work rule. Strikes within a site's warn/stop radius call
raise_site_alert(), which restarts the 30-minute all-clear on every new strike.
"""
import datetime as dt
import io
import re

import h5py
import httpx
import numpy as np

BUCKET = "https://noaa-goes19.s3.amazonaws.com"
client = httpx.Client(timeout=60)

RAISE_FROM_STRIKES = """
SELECT count(raise_site_alert(s.site_id, 'lightning',
                              CASE WHEN ST_DWithin(l.geog, s.geog, c.stop_radius_mi * 1609.344)
                                   THEN 'stop_work' ELSE 'warning' END,
                              l.observed_at, round((ST_Distance(l.geog, s.geog) / 1609.344)::numeric, 1),
                              'Lightning ' || round((ST_Distance(l.geog, s.geog) / 1609.344)::numeric, 1) || ' mi away'))
  FROM (SELECT * FROM lightning_strikes WHERE observed_at > %s ORDER BY observed_at) l
  JOIN hazard_categories c ON c.category = 'lightning'
  JOIN sites s ON ST_DWithin(l.geog, s.geog, c.warn_radius_mi * 1609.344)
"""


def file_start(key):
    """...OR_GLM-L2-LCFA_G19_s20262900540000_e... -> 2026-10-17 05:40:00 UTC (year, day of year, H M S, tenths)."""
    s = re.search(r"_s(\d{13})", key).group(1)
    return dt.datetime.strptime(s, "%Y%j%H%M%S").replace(tzinfo=dt.timezone.utc)


def unpack(var):
    """Apply the netCDF _Unsigned / scale_factor / add_offset packing that h5py leaves alone."""
    raw = var[()]
    if var.attrs.get("_Unsigned", b"") in (b"true", "true"):
        raw = raw.view(np.uint16)
    return raw * var.attrs.get("scale_factor", [1])[0] + var.attrs.get("add_offset", [0])[0]


def parse(nc_bytes):
    """GLM L2 file -> [(time, lat, lon, energy_fj, quality_flag)]."""
    with h5py.File(io.BytesIO(nc_bytes), "r") as f:
        start = dt.datetime.fromisoformat(f.attrs["time_coverage_start"].decode().replace("Z", "+00:00"))
        offsets, energy = unpack(f["flash_time_offset_of_first_event"]), unpack(f["flash_energy"])
        return [(start + dt.timedelta(seconds=float(t)), float(la), float(lo), float(e) * 1e15, int(q))
                for t, la, lo, e, q in zip(offsets, f["flash_lat"][()], f["flash_lon"][()], energy,
                                           f["flash_quality_flag"][()])]


def new_files(since):
    hours = sorted({since.replace(minute=0, second=0, microsecond=0),
                    dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)})
    keys = []
    for h in hours:
        listing = client.get(BUCKET, params={"list-type": "2", "prefix": f"GLM-L2-LCFA/{h:%Y/%j/%H}/"}).text
        keys += re.findall(r"<Key>([^<]+)</Key>", listing)
    return [k for k in keys if file_start(k) + dt.timedelta(seconds=20) > since]


def raise_alerts(conn, since):
    return conn.execute(RAISE_FROM_STRIKES, (since,)).fetchone()["count"]


def sync(conn):
    """Pull GLM files newer than the last stored strike, keep flashes near the sites, raise alerts."""
    since = conn.execute("SELECT greatest(max(observed_at), now() - interval '3 minutes') AS t FROM lightning_strikes "
                         "WHERE observed_at > now() - interval '10 minutes'").fetchone()["t"] \
        or dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)
    box = conn.execute("SELECT min(ST_Y(geog::geometry)) - 0.5 AS s, max(ST_Y(geog::geometry)) + 0.5 AS n, "
                       "min(ST_X(geog::geometry)) - 0.5 AS w, max(ST_X(geog::geometry)) + 0.5 AS e FROM sites").fetchone()
    strikes = []
    for key in new_files(since):
        for t, lat, lon, energy, flag in parse(client.get(f"{BUCKET}/{key}").content):
            if t > since and box["s"] <= lat <= box["n"] and box["w"] <= lon <= box["e"]:
                strikes.append((t, f"SRID=4326;POINT({lon} {lat})", energy, flag))
    with conn.cursor().copy("COPY lightning_strikes (observed_at, geog, energy_fj, quality_flag) FROM STDIN") as cp:
        for s in strikes:
            cp.write_row(s)
    return f"{len(strikes)} strikes near sites, {raise_alerts(conn, since)} site alerts raised or extended"
