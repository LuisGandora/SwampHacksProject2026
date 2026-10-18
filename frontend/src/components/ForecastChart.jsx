import { useRef, useState } from "react";

// Next-48-hour forecast as small multiples: heat index (°F), rain (in/h), chance of thunder (%), each on its
// own axis (never a dual axis), one shared hover crosshair. Single series per chart, so no legend: the title
// names it. A table view is one click away.
const W = 340, ROW = 52, PAD_L = 36, PAD_R = 8, PAD_T = 14, BLUE = "#3987e5";

export default function ForecastChart({ rows, timezone, title = "Next 48 hours (NWS)", nowIndex = null }) {
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);
  const svg = useRef(null);
  if (!rows?.length) return <p className="muted small">No forecast yet. The worker loads NWS forecasts hourly.</p>;

  const n = rows.length;
  const x = (i) => PAD_L + (i * (W - PAD_L - PAD_R)) / Math.max(n - 1, 1);
  const hour = (r) => new Date(r.valid_at).toLocaleString("en-US", { timeZone: timezone, weekday: "short", hour: "numeric" });
  const series = [
    { key: "heat", title: "Heat index °F", get: (r) => r.heat_index_f ?? r.temp_f, kind: "line", fmt: (v) => `${Math.round(v)}°F`, step: 5 },
    { key: "rain", title: "Rain in/h", get: (r) => r.precip_in, kind: "bar", fmt: (v) => `${Number(v).toFixed(2)} in`, step: 0.05, floor: 0.1 },
    { key: "thunder", title: "Thunder %", get: (r) => r.thunder_chance, kind: "bar", fmt: (v) => `${Math.round(v)}%`, max: 100 },
  ].map((s) => ({ ...s, fmt: (v) => v == null ? "unavailable" : s.fmt(v) }));

  const onMove = (e) => {
    const box = svg.current.getBoundingClientRect();
    const px = ((e.touches?.[0]?.clientX ?? e.clientX) - box.left) * (W / box.width);
    setHover(Math.max(0, Math.min(n - 1, Math.round(((px - PAD_L) / (W - PAD_L - PAD_R)) * (n - 1)))));
  };
  const height = PAD_T + series.length * ROW + 16;
  const midnights = rows.map((r, i) => [r, i]).filter(([r]) =>
    Number(new Date(r.valid_at).toLocaleString("en-US", { timeZone: timezone, hour: "numeric", hourCycle: "h23" })) === 0);
  const dayTicks = midnights.filter((_, k) => midnights.length <= 8 || k % 2 === 0);  // two-week view: every other day

  return (
    <div className="forecast">
      <div className="forecast-head">
        <h4>{title}</h4>
        <button className="link" onClick={() => setTable((t) => !t)}>{table ? "Chart" : "Table"}</button>
      </div>
      {table ? (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Hour</th>{series.map((s) => <th key={s.key}>{s.title}</th>)}</tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.valid_at}><td>{hour(r)}</td>{series.map((s) => <td key={s.key}>{s.fmt(s.get(r))}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div className="chart-wrap">
          <svg ref={svg} viewBox={`0 0 ${W} ${height}`} className="chart" onMouseMove={onMove} onTouchMove={onMove}
               onMouseLeave={() => setHover(null)} role="img" aria-label="Forecast for the next 48 hours">
            {series.map((s, si) => {
              const vals = rows.map(s.get);
              const known = vals.filter((v) => v != null);
              const top = PAD_T + si * ROW, h = ROW - 18;
              // nice per-chart scale: bars start at 0; heat rounds to 5 °F, rain to 0.05 in, thunder is 0-100 %
              const lo = s.kind === "bar" ? 0 : Math.floor(Math.min(...(known.length ? known : [0])) / s.step) * s.step;
              const hi = s.max ?? (Math.max(s.floor ?? 0, Math.ceil(Math.max(...(known.length ? known : [0])) / s.step) * s.step) || 1);
              const y = (v) => top + h - ((v - lo) / Math.max(hi - lo, 1e-9)) * h;
              const bw = Math.max(2, (W - PAD_L - PAD_R) / n - 2);
              return (
                <g key={s.key}>
                  <text x={PAD_L} y={top - 3} className="chart-title">{s.title}</text>
                  <line x1={PAD_L} x2={W - PAD_R} y1={top + h} y2={top + h} className="axis" />
                  <text x={PAD_L - 4} y={top + 8} className="tick" textAnchor="end">{s.fmt(hi).replace(/ in|°F|%/, "")}</text>
                  <text x={PAD_L - 4} y={top + h} className="tick" textAnchor="end">{s.fmt(lo).replace(/ in|°F|%/, "")}</text>
                  {s.kind === "line" ? (
                    <path d={vals.map((v, i) => v == null ? "" : `${i && vals[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("")}
                          fill="none" stroke={BLUE} strokeWidth="2" strokeLinejoin="round" />
                  ) : vals.map((v, i) => v > 0 && (
                    <rect key={i} x={x(i) - bw / 2} y={y(v)} width={bw} height={top + h - y(v)} rx="1.5" fill={BLUE} />
                  ))}
                  {hover != null && vals[hover] != null && (
                    <circle cx={x(hover)} cy={y(vals[hover])} r="4" fill={BLUE} stroke="#14171c" strokeWidth="2" />
                  )}
                </g>
              );
            })}
            {dayTicks.map(([r, i]) => (
              <text key={r.valid_at} x={x(i)} y={height - 3} className="tick" textAnchor="middle">
                {new Date(r.valid_at).toLocaleString("en-US", { timeZone: timezone, weekday: "short" })}
              </text>
            ))}
            {nowIndex != null && nowIndex > 0 && (
              <g>
                <line x1={x(nowIndex)} x2={x(nowIndex)} y1={PAD_T - 8} y2={height - 14} className="now-line" />
                <text x={x(nowIndex) - 6} y={PAD_T - 6} className="tick" textAnchor="end">measured</text>
                <text x={x(nowIndex) + 6} y={PAD_T - 6} className="tick">forecast</text>
              </g>
            )}
            {hover != null && <line x1={x(hover)} x2={x(hover)} y1={PAD_T - 8} y2={height - 14} className="crosshair" />}
          </svg>
          {hover != null && (
            <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
              <strong>{hour(rows[hover])}</strong>
              {series.map((s) => <span key={s.key}>{s.title.split(" ")[0]}: {s.fmt(s.get(rows[hover]))}</span>)}
              {rows[hover].short_forecast && <span className="muted">{rows[hover].short_forecast}</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
