import { useState } from "react";
import { pixelToLatLon, sitesWithin } from "../geo.js";

const MAP = { width: 850, height: 1100 };

export default function Landing({ sites = [], onDone }) {
  const [useGps, setUseGps] = useState(false);
  const [place, setPlace] = useState(null); // { lat, lon }
  const [pin, setPin] = useState(null);     // { fx, fy } fractions, for drawing
  const [radius, setRadius] = useState(100);
  const [status, setStatus] = useState("");

  const toggleGps = (e) => {
    const on = e.target.checked;
    setUseGps(on);
    setPlace(null);
    setPin(null);
    setStatus("");
    if (!on) return;
    if (!navigator.geolocation) {
      setUseGps(false);
      setStatus("This browser can't share location. Drop a pin on the map instead.");
      return;
    }
    setStatus("Finding your location...");
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            setPlace({ lat: pos.coords.latitude, lon: pos.coords.longitude });
            setStatus("Finding your location (this can take a few seconds)...");
        },
        (err) => {
            setUseGps(false);
            setStatus(`Couldn't get your location (${err.message}). Drop a pin on the map instead.`);
        },
        {
            enableHighAccuracy: false, 
            timeout: 8000, 
            maximumAge: 10 * 60 * 1000 
        }
    );
  };

  const dropPin = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width;
    const fy = (e.clientY - r.top) / r.height;
    setPin({ fx, fy });
    setPlace(pixelToLatLon(fx * MAP.width, fy * MAP.height));
  };

  const count = place ? sitesWithin(sites, { ...place, radius }).length : 0;

  return (
    <div className="landing">
      <h1>Plan It By Planet</h1>
      <p className="landing-intro">
        See hazard levels for power plants near you. Choose how to set your
        location, then pick how far out to look.
      </p>

      <label className="switch-row">
        <input type="checkbox" checked={useGps} onChange={toggleGps} />
        <span className="switch" aria-hidden="true" />
        <span>Use my current location</span>
      </label>
      {status && <p className="landing-status">{status}</p>}

      {!useGps && (
        <div className="pin-section">
          <p>
            {pin
              ? "Pin placed. Click again to move it."
              : "Click the map to drop a pin where you want to look."}
          </p>
          <div className="pin-map" onClick={dropPin}>
            <img src="/map.png" alt="Map of Alabama, Georgia and Florida" />
            {pin && (
              <div
                className="drop-pin"
                style={{ left: `${pin.fx * 100}%`, top: `${pin.fy * 100}%` }}
              />
            )}
          </div>
        </div>
      )}

      {place && (
        <div className="radius-section">
          <label htmlFor="radius">
            Show power plants within <strong>{radius} miles</strong>
          </label>
          <input
            id="radius"
            type="range"
            min="5"
            max="100"
            step="5"
            value={radius}
            onChange={(e) => setRadius(Number(e.target.value))}
          />
          <p className="landing-status">
            {count} power plant{count === 1 ? "" : "s"} in range
          </p>
        </div>
      )}

      <div className="landing-actions">
        <button
          className="landing-go"
          disabled={!place}
          onClick={() => onDone({ ...place, radius })}
        >
          Show power plants
        </button>
        <button className="landing-skip" onClick={() => onDone({ all: true })}>
          Skip and show everything
        </button>
      </div>
    </div>
  );
}