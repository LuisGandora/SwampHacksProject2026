"""USACE National Levee Database (NLD) for FL/GA/AL: leveed areas, levee alignment lines and soil borings.

Public OGC WFS (no key): https://levees.sec.usace.army.mil/mapserver/public/ows . It only speaks GML, so this reads
the XML and writes data/levees.geojson (lon/lat). `python -m db.load_levees` loads it into Tiger, where each site
gets: inside a leveed area (and that area's FEMA accreditation, overtopping chance, people protected), distance to
the nearest levee, and soil borings nearby. Run from the project root:  python scraper/levees.py
"""
import json
import urllib.parse
import xml.etree.ElementTree as ET

from scrape_power_plants import DATA, get

WFS = "https://levees.sec.usace.army.mil/mapserver/public/ows"
BBOX = "24.3,-88.6,35.1,-79.9,EPSG:4326"   # FL/GA/AL (lat,lon order in WFS 1.1.0)
NS = {"gml": "http://www.opengis.net/gml", "ms": "http://mapserver.gis.umn.edu/mapserver",
      "wfs": "http://www.opengis.net/wfs"}
LAYERS = {  # NLD layer -> (our kind, attributes kept)
    "ms:leveed_area": ("leveed_area", ["SYSTEM_ID", "SYSTEM_NAME", "FEATURE_NAME", "FEMA_ACCREDITATION_RATING",
                                       "OVERTOPPING_AEP", "PRIMARY_FLOOD_SOURCE", "PEOPLE", "BUILDINGS",
                                       "PROPERTY_VALUE", "WARN_IND", "EVACUATION_PLAN_IND", "RP_STATUS"]),
    "ms:system_alignment_lines": ("levee_line", ["SYSTEM_ID", "SYSTEM_NAME", "SEGMENT_NAME", "ALIGN_TYPE"]),
    "ms:boreholes": ("borehole", ["SYSTEM_ID", "SYSTEM_NAME", "BOREHOLE_ID", "TYPE", "TOTAL_DEPTH",
                                  "GROUND_SURF_ELEVATION", "COMPLETION_DATE"]),
}


def coords(pos_list):
    """GML posList "lat lon lat lon ..." -> [[lon, lat], ...]"""
    v = [float(x) for x in pos_list.split()]
    return [[v[i + 1], v[i]] for i in range(0, len(v), 2)]


def geometry(el):
    """First geometry under a feature as GeoJSON (MultiSurface/Polygon, MultiCurve/LineString, Point)."""
    polys = [[coords(r.text) for r in p.iter(f"{{{NS['gml']}}}posList")] for p in el.iter(f"{{{NS['gml']}}}Polygon")]
    if polys:
        return {"type": "MultiPolygon", "coordinates": [[ring for ring in p] for p in polys]}
    lines = [coords(p.text) for p in el.iter(f"{{{NS['gml']}}}posList")]
    if lines:
        return {"type": "MultiLineString", "coordinates": lines}
    pos = next(el.iter(f"{{{NS['gml']}}}pos"), None)
    if pos is not None:
        return {"type": "Point", "coordinates": coords(pos.text)[0]}
    return None


def fetch(layer, kind, keep):
    q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature", "TYPENAME": layer,
                                "BBOX": BBOX, "MAXFEATURES": 20000})
    root = ET.fromstring(get(f"{WFS}?{q}"))
    out = []
    for member in root.iter(f"{{{NS['gml']}}}featureMember"):
        feat = next(iter(member))
        geom = geometry(feat)
        if geom is None:
            continue
        props = {k: (feat.findtext(f"ms:{k}", namespaces=NS) or None) for k in keep}
        out.append({"type": "Feature", "geometry": geom, "properties": {"kind": kind, **props}})
    print(f"  {kind}: {len(out)}", flush=True)
    return out


def main():
    features = [f for layer, (kind, keep) in LAYERS.items() for f in fetch(layer, kind, keep)]
    (DATA / "levees.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(f"Wrote {len(features)} NLD features to {DATA / 'levees.geojson'}")


if __name__ == "__main__":
    main()
