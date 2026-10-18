import { CloudRain, MapPin, TriangleAlert } from "lucide-react";
import { BANDS, NO_SCORE, NWS_COLORS, NWS_OTHER } from "../hazard.js";
import { PIN_COLORS } from "./GlobeMap.jsx";

// NEXRAD reflectivity colors, light rain to hail, as drawn by the radar tiles.
const RADAR_SCALE = ["#04e9e7", "#019ff4", "#0300f4", "#02fd02", "#01c501", "#008e00", "#fdf802", "#e5bc00",
                     "#fd9500", "#fd0000", "#d40000", "#bc0000", "#f800fd"];

// Map layer switches plus a legend for every icon currently on the map. Every swatch has a text label.
export default function MapLayers({ layers, setLayer, pinCount, warnings, onClose }) {
  const events = Object.entries((warnings?.features || []).reduce((acc, f) => {
    acc[f.properties.event] = (acc[f.properties.event] || 0) + 1;
    return acc;
  }, {})).sort((a, b) => b[1] - a[1]);
  const toggle = (key, label, hint) => (
    <label className="layer-toggle">
      <input type="checkbox" checked={layers[key]} onChange={(e) => setLayer(key, e.target.checked)} />
      <span>{label}</span>{hint && <span className="muted small">{hint}</span>}
    </label>
  );

  return (
    <section className="map-layers panel" aria-label="Map layers and legend">
      <header className="panel-head">
        <h3>Layers</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close layers">✕</button>
      </header>
      <div className="layer-toggles">
        {toggle("radar", <><CloudRain className="ico" aria-hidden="true" /> Radar</>, "live, every 5 min")}
        {toggle("warnings", <><TriangleAlert className="ico" aria-hidden="true" /> NWS warnings</>, "active now")}
        {toggle("pins", <><MapPin className="ico" aria-hidden="true" /> Hazard pins</>, "past reports")}
      </div>

      <h4>Sites</h4>
      <ul className="legend">
        {[...BANDS, NO_SCORE].map((b) => (
          <li key={b.key}><i className="dot" style={{ background: b.color }} /> {b.label}{b.min != null ? ` (${b.min}+)` : ""}</li>
        ))}
        <li className="small muted">Gray (no score): a LIVE ONLY site outside FL/GA/AL with no alert right now. The Hazard Index
          model covers the FL/GA/AL study area; elsewhere a site takes a color only from a live alert.</li>
        <li><i className="dot dot--ring" style={{ borderColor: "#d03b3b" }} /> Pulsing ring: stop work now</li>
        <li><i className="dot" style={{ background: "#3987e5", boxShadow: "0 0 0 3px rgba(57,135,229,.3)" }} /> You are here</li>
        <li><i className="dot" style={{ background: "#fff3a3" }} /> Lightning, last 30 min (fades with age)</li>
      </ul>

      {layers.radar && (
        <>
          <h4>Radar (precipitation)</h4>
          <div className="radar-scale" aria-hidden="true">
            {RADAR_SCALE.map((c) => <span key={c} style={{ background: c }} />)}
          </div>
          <div className="radar-labels small"><span>Light</span><span>Moderate</span><span>Heavy</span><span>Hail</span></div>
        </>
      )}

      {layers.warnings && (
        <>
          <h4>NWS warnings</h4>
          {events.length === 0 ? <p className="muted small">No active NWS alerts in the region.</p> : (
            <ul className="legend">
              {events.map(([event, n]) => (
                <li key={event}>
                  <i className="dot dot--ring" style={{ borderColor: NWS_COLORS[event] || NWS_OTHER }} /> {event}
                  <span className="muted"> ×{n}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="muted small">Shaded areas: storm-based warnings. Rings: county or zone alerts, at each site they cover.
            Tap one for details.</p>
        </>
      )}

      {layers.pins && (
        <>
          <h4>Hazard pins ({pinCount.toLocaleString()})</h4>
          <ul className="legend">
            <li><i className="dot" style={{ background: PIN_COLORS.crew }} /> Crew report (faded = closed)</li>
            <li><i className="dot dot--ring" style={{ borderColor: PIN_COLORS.osha }} /> Historical OSHA injury report (not live)</li>
          </ul>
        </>
      )}
    </section>
  );
}
