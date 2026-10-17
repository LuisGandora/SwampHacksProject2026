import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { BAND_COLOR_EXPRESSION, band, fmtScore } from "../hazard.js";

const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const NORTH_AMERICA = { center: [-97, 39.5], zoom: 3.1 };
const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Keyless Esri satellite imagery on a globe, with boundary/place and road labels on top.
const STYLE = {
  version: 8,
  projection: { type: "globe" },
  sky: { "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] },
  sources: {
    imagery: {
      type: "raster", tileSize: 256, maxzoom: 19,
      tiles: [`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`],
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
    },
    places: {
      type: "raster", tileSize: 256, maxzoom: 19,
      tiles: [`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`],
    },
    roads: {
      type: "raster", tileSize: 256, maxzoom: 19, minzoom: 6,
      tiles: [`${ESRI}/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}`],
    },
  },
  layers: [
    { id: "space", type: "background", paint: { "background-color": "#05070a" } },
    { id: "imagery", type: "raster", source: "imagery" },
    { id: "roads", type: "raster", source: "roads", minzoom: 7, paint: { "raster-opacity": 0.55 } },
    { id: "places", type: "raster", source: "places", paint: { "raster-opacity": 0.85 } },
  ],
};

const empty = { type: "FeatureCollection", features: [] };
const point = (lon, lat, properties = {}) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties });

const GlobeMap = forwardRef(function GlobeMap(
  { sites, lightning, selectedId, onSelect, pinMode, pin, onPin, me, sensor }, ref) {
  const container = useRef(null);
  const map = useRef(null);
  const ready = useRef(false);
  const hasStops = useRef(false);
  const handlers = useRef({ onSelect, onPin, pinMode });
  handlers.current = { onSelect, onPin, pinMode };

  useImperativeHandle(ref, () => ({
    flyHome: () => map.current?.flyTo({ ...NORTH_AMERICA, duration: reducedMotion() ? 0 : 2500 }),
    flySites: () => {
      if (!map.current || !sites.length) return;
      const b = new maplibregl.LngLatBounds();
      sites.forEach((s) => b.extend([s.lon, s.lat]));
      map.current.fitBounds(b, { padding: 60, duration: reducedMotion() ? 0 : 2000 });
    },
    flyRadius: (lon, lat, miles) => {
      if (!map.current) return;
      const dLat = miles / 69;
      const dLon = miles / (69 * Math.cos((lat * Math.PI) / 180));
      map.current.fitBounds(
        [[lon - dLon, lat - dLat], [lon + dLon, lat + dLat]],
        { padding: 60, maxZoom: 10, duration: reducedMotion() ? 0 : 2000 }
      );
    },
    flyTo: (lon, lat, zoom = 9) => map.current?.flyTo({ center: [lon, lat], zoom, duration: reducedMotion() ? 0 : 1800 }),
  }), [sites]);

  // Create the map once.
  useEffect(() => {
    let pulseTimer = null;
    const m = new maplibregl.Map({
      container: container.current, style: STYLE, center: [-40, 18], zoom: 1.1,
      attributionControl: { compact: true }, maxPitch: 70,
    });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

    m.on("load", () => {
      m.addSource("sites", { type: "geojson", data: empty });
      m.addSource("lightning", { type: "geojson", data: empty });
      m.addSource("selected", { type: "geojson", data: empty });
      m.addLayer({ id: "alert-pulse", type: "circle", source: "sites", filter: [">", ["get", "stop_work_alerts"], 0],
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#d03b3b", "circle-stroke-width": 2,
                 "circle-radius": 10, "circle-stroke-opacity": 0.9 } });
      m.addLayer({ id: "lightning", type: "circle", source: "lightning",
        paint: { "circle-color": "#fff3a3", "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2, 9, 5],
                 "circle-opacity": ["interpolate", ["linear"], ["get", "age_min"], 0, 1, 30, 0.15],
                 "circle-blur": 0.4 } });
      m.addLayer({ id: "sites", type: "circle", source: "sites",
        paint: { "circle-color": BAND_COLOR_EXPRESSION,
                 "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 2.5, 5, 4.5, 9, 8, 13, 11],
                 "circle-stroke-color": "#0b0d10", "circle-stroke-width": 1.5 } });
      m.addLayer({ id: "selected", type: "circle", source: "selected",
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": 14, "circle-stroke-color": "#f0efec",
                 "circle-stroke-width": 2 } });

      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "hud-popup" });
      m.on("mousemove", "sites", (e) => {
        m.getCanvas().style.cursor = handlers.current.pinMode ? "crosshair" : "pointer";
        const p = e.features[0].properties;
        const b = band(p.score === "" || p.score == null ? null : Number(p.score));
        popup.setLngLat(e.features[0].geometry.coordinates)
          .setHTML(`<strong>${p.name}</strong><br/>${p.state} · Hazard ${fmtScore(p.score === "" ? null : Number(p.score))}/10 ` +
                   `<span style="color:${b.color}">● ${b.label}</span>` +
                   (Number(p.stop_work_alerts) ? `<br/><span class="stop">STOP WORK ×${p.stop_work_alerts}</span>` : ""))
          .addTo(m);
      });
      m.on("mouseleave", "sites", () => {
        m.getCanvas().style.cursor = handlers.current.pinMode ? "crosshair" : "";
        popup.remove();
      });
      m.on("click", (e) => {
        if (handlers.current.pinMode) return handlers.current.onPin({ lat: e.lngLat.lat, lon: e.lngLat.lng });
        const hit = m.queryRenderedFeatures(e.point, { layers: ["sites"] })[0];
        if (hit) handlers.current.onSelect(Number(hit.properties.site_id));
      });

      // Pulse the stop-work rings at 20 fps, and only while a stop-work alert exists: every frame redraws the
      // globe, so an always-on animation would keep a phone's GPU busy for nothing.
      let t = 0;
      pulseTimer = reducedMotion() ? null : setInterval(() => {
        if (!hasStops.current || !map.current) return;
        t = (t + 1) % 30;
        m.setPaintProperty("alert-pulse", "circle-radius", 8 + (t / 30) * 16);
        m.setPaintProperty("alert-pulse", "circle-stroke-opacity", 0.9 * (1 - t / 30));
      }, 50);

      ready.current = true;
      m.fire("planit:ready");
      m.flyTo({ ...NORTH_AMERICA, duration: reducedMotion() ? 0 : 4000, essential: true });
    });
    return () => { clearInterval(pulseTimer); map.current = null; ready.current = false; m.remove(); };
  }, []);

  // Keep the data layers in sync with props.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    hasStops.current = sites.some((s) => s.stop_work_alerts > 0);
    const apply = () => {
      m.getSource("sites")?.setData({ type: "FeatureCollection", features: sites.map((s) => point(s.lon, s.lat, {
        site_id: s.site_id, name: s.name, state: s.state, score: s.score, stop_work_alerts: s.stop_work_alerts,
      })) });
      const now = Date.now();
      m.getSource("lightning")?.setData({ type: "FeatureCollection", features: lightning.map((l) => point(l.lon, l.lat, {
        age_min: (now - new Date(l.observed_at).getTime()) / 60000 })) });
      const sel = sites.find((s) => s.site_id === selectedId);
      m.getSource("selected")?.setData(sel ? { type: "FeatureCollection", features: [point(sel.lon, sel.lat)] } : empty);
    };
    if (ready.current) apply(); else m.once("planit:ready", apply);
  }, [sites, lightning, selectedId]);

  // Draft report pin and the viewer's own location, as DOM markers.
  useMarker(map, pin, "marker marker--pin", "Report location");
  useMarker(map, me, "marker marker--me", "You are here");

  useEffect(() => {
    if (map.current) map.current.getCanvas().style.cursor = pinMode ? "crosshair" : "";
  }, [pinMode]);

  return <div ref={container} className={`globe sensor-${sensor}`} role="application"
              aria-label="Globe map of power plant sites, colored by hazard index" />;
});

function useMarker(map, at, className, title) {
  const marker = useRef(null);
  useEffect(() => {
    if (!map.current) return;
    if (!at) { marker.current?.remove(); marker.current = null; return; }
    if (!marker.current) {
      const el = document.createElement("div");
      el.className = className;
      el.title = title;
      marker.current = new maplibregl.Marker({ element: el });
    }
    marker.current.setLngLat([at.lon, at.lat]).addTo(map.current);
  }, [map, at?.lat, at?.lon]);
  useEffect(() => () => marker.current?.remove(), []);
}

export default GlobeMap;
