import { useState } from "react";
import { CITIES, MAP_SIZE } from "./data.js";

// Green (safe) -> red (hazardous) for a 0-10 index.
const hazardColor = (v) => `hsl(${120 - v * 12}, 70%, 42%)`;

export default function App() {
  const [cities, setCities] = useState(CITIES);
  const [selectedName, setSelectedName] = useState(null);
  const [rankOpen, setRankOpen] = useState(false);

  const selected = cities.find((c) => c.name === selectedName) ?? null;

  const setHazard = (value) =>
    setCities((prev) =>
      prev.map((c) => (c.name === selectedName ? { ...c, hazard: value } : c))
    );

  return (
    <div className="page">
      <header className="header">
        <h1 className="title">Plan It By Planet</h1>
        <p className="index">
          Hazard Index:{" "}
          {selected ? (
            <strong style={{ color: hazardColor(selected.hazard) }}>
              {selected.hazard} / 10
            </strong>
          ) : (
            "0 - 10"
          )}
        </p>
      </header>

      <main className="layout">
        <section className="map-wrap" aria-label="Map of Alabama, Georgia and Florida">
          <div
            className="map"
            style={{ aspectRatio: `${MAP_SIZE.width} / ${MAP_SIZE.height}` }}
          >
            <img src="/map.png" alt="Map of Alabama, Georgia and Florida" />
            {cities.map((c) => (
              <button
                key={c.name}
                className={`pin ${c.name === selectedName ? "pin--active" : ""}`}
                style={{
                  left: `${(c.x / MAP_SIZE.width) * 100}%`,
                  top: `${(c.y / MAP_SIZE.height) * 100}%`,
                  background: hazardColor(c.hazard),
                }}
                onClick={() => {
                  setSelectedName(c.name);
                  setRankOpen(false);
                }}
                aria-label={`${c.name}, hazard index ${c.hazard} of 10`}
                title={c.name}
              />
            ))}
          </div>
        </section>

        <aside className="panel">
          <div className="info">
            {selected ? (
              <>
                <h2 className="city">{selected.name}</h2>
                {selected.info.map((line) => (
                  <p key={line} className="info-line">
                    {line}
                  </p>
                ))}
              </>
            ) : (
              <>
                <p className="info-line">General Info 1</p>
                <p className="info-line">General Info 2</p>
                <p className="info-line">General Info 3</p>
                <p className="hint">Select a city on the map to see its details.</p>
              </>
            )}
          </div>

          <div className="supervisor">
            <button
              className="rank-btn"
              disabled={!selected}
              onClick={() => setRankOpen((o) => !o)}
            >
              SUPERVISOR RANK BUTTON
            </button>

            {rankOpen && selected && (
              <div className="rank-picker" role="group" aria-label="Set hazard rank">
                <p>Set hazard rank for {selected.name}</p>
                <div className="rank-options">
                  {Array.from({ length: 11 }, (_, n) => (
                    <button
                      key={n}
                      className={n === selected.hazard ? "on" : ""}
                      onClick={() => setHazard(n)}
                    >
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
