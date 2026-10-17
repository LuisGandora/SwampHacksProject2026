from scrape_power_plants import match_osm

osm = [
    {"osm_id": "way/1", "lat": 30.0, "lon": -85.0, "tags": {"ref:US:EIA": "10;11", "name": "Ref Plant"}},
    {"osm_id": "way/2", "lat": 31.0, "lon": -86.0, "tags": {"name": "Near Plant"}},
]
plants = [
    {"eia_plant_id": 11, "lat": 30.05, "lon": -85.0},   # ref match, ~5.6 km off -> review
    {"eia_plant_id": 20, "lat": 31.001, "lon": -86.0},  # nearest match, ~0.1 km -> ok
    {"eia_plant_id": 30, "lat": 25.0, "lon": -80.0},    # nothing nearby
    {"eia_plant_id": 10, "lat": None, "lon": None},     # missing coords filled from OSM
]
match_osm(plants, osm)
a, b, c, d = plants
assert (a["osm_match"], a["coord_check"]) == ("eia_ref", "review"), a
assert (b["osm_id"], b["osm_match"], b["coord_check"]) == ("way/2", "nearest", "ok"), b
assert (c["osm_match"], c["coord_check"]) == ("none", "no_osm"), c
assert (d["lat"], d["coord_source"], d["osm_distance_km"]) == (30.0, "osm", 0.0), d
print("ok")

from relate_weather import relate

plant = {"eia_plant_id": "1", "lat": "30.0", "lon": "-85.0", "county_fips": "12005"}
events = [
    {"id": "near", "points": [(30.2, -85.0)], "fips": set(), "how": "point"},     # ~13.8 mi
    {"id": "far", "points": [(31.0, -85.0)], "fips": set(), "how": "point"},      # ~69 mi
    {"id": "zone", "points": [(31.0, -85.0)], "fips": {"12005"}, "how": "zone"},  # far centroid, but zone covers plant's county
    {"id": "tornado", "points": [(32.0, -85.0), (30.1, -85.0)], "fips": set(), "how": "point"},  # path end is near
]
hits = {e["id"]: d for _, e, d, _ in relate([plant], events, 25)}
assert set(hits) == {"near", "zone", "tornado"}, hits
assert hits["near"] == 13.8 and hits["tornado"] == 6.9, hits
print("relate ok")

from crew_reports import categorize

base = {"Final Narrative": "", "EventTitle": "", "Source": "", "Loss of Eye": "0", "Inspection": ""}
case = lambda **kw: set(categorize({**base, **{k.replace("_", " "): v for k, v in kw.items()}}))
assert case(Final_Narrative="He was not wearing fall protection.") == {("ppe", "narrative")}
assert case(EventTitle="Other fall to lower level 6 to 10 feet") == {("ppe", "injury_type")}
assert case(Source="3211") == {("heavy_machinery", "source")}
assert case(EventTitle="Fall on same level due to slipping", Final_Narrative="slipped on spilled oil") == {("slip_trip", "narrative")}
assert case(EventTitle="Fall on same level due to tripping over an object") == {("slip_trip", "injury_type")}
assert case(Inspection="1018519", Loss_of_Eye="1.00") == {("osha_inspection", "inspection_opened"), ("ppe", "injury_type")}
assert case(EventTitle="Exposure to environmental heat") == set()
print("categorize ok")

from scrape_power_plants import resolve_coordinates, same_name

assert same_name("Kayak", "Kayak Solar") and not same_name("FL Solar 4, LLC", "Solar Farm")
far = lambda **kw: {"coord_check": "review", "osm_distance_km": "40", "osm_id": "way/1", "osm_name": "",
                    "name": "X", "lat": 1, "lon": 1, "_osm": (2, 2), "county_matches_eia": True, **kw}
traced, wrong_county, unnamed = far(name="Kayak", osm_name="Kayak Solar"), far(county_matches_eia=False), far()
moved = resolve_coordinates([traced, wrong_county, unnamed, far(osm_distance_km="3")])
assert moved == [traced, wrong_county] and traced["lat"] == 2 and traced["coord_check"] == "corrected"
assert unnamed["coord_check"] == "unresolved" and unnamed["lat"] == 1
print("resolve ok")
