// Hazard Index bands use the fixed status palette (good / warning / serious / critical) and always ship
// with a text label, so a band is never told by color alone.
export const BANDS = [
  { min: 7.5, key: "critical", label: "Critical", color: "#d03b3b" },
  { min: 5, key: "serious", label: "High", color: "#ec835a" },
  { min: 2.5, key: "warning", label: "Moderate", color: "#fab219" },
  { min: 0, key: "good", label: "Low", color: "#0ca30c" },
];
export const NO_SCORE = { key: "none", label: "No score", color: "#7a7a75" };

export const band = (score) => (score == null ? NO_SCORE : BANDS.find((b) => score >= b.min) ?? BANDS.at(-1));

// MapLibre expression with the same bands, for the site dots on the globe.
export const BAND_COLOR_EXPRESSION = [
  "step", ["coalesce", ["get", "score"], -1],
  NO_SCORE.color, 0, BANDS[3].color, 2.5, BANDS[2].color, 5, BANDS[1].color, 7.5, BANDS[0].color,
];

export const fmtScore = (s) => (s == null || s === "" || !Number.isFinite(Number(s)) ? "–" : Number(s).toFixed(1));  // no score -> "–", never "NaN"

const FALLBACK_LABELS = {
  severe_storm: "Severe storm", tropical: "Tropical", flooding: "Flooding", lightning: "Lightning",
  extreme_heat: "Extreme heat", extreme_cold: "Extreme cold", high_wind: "High wind", wildfire: "Wildfire",
  soil_saturation: "Soil saturation", wildlife: "Wildlife", dense_fog: "Dense fog", coastal: "Coastal",
  ppe: "PPE", heavy_machinery: "Heavy machinery", slip_trip: "Slip / trip", osha_inspection: "OSHA",
};
export const categoryLabel = (key, categories = []) =>
  categories.find((c) => c.category === key)?.label ?? FALLBACK_LABELS[key] ?? key?.replace(/_/g, " ");

// "12:34" countdown from an ISO time, or null once it has passed.
export function countdown(isoUntil, now = Date.now()) {
  if (!isoUntil) return null;
  const s = Math.round((new Date(isoUntil).getTime() - now) / 1000);
  if (!Number.isFinite(s) || s <= 0) return null;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(sec).padStart(2, "0")}`;
}

// Expiration ends an alert record; it does not establish that conditions are safe.
export function alertTiming(a, now = Date.now()) {
  const left = countdown(a.all_clear_at, now);
  if (!left) return null;
  if (a.category === "lightning") return `Lightning hold: ${left} remaining; verify conditions before resuming`;
  const end = new Date(a.all_clear_at);
  return `Alert expires ${end.toLocaleString([], { timeZone: a.timezone, month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short" })}; may be extended`;
}

export const fmtCrewHours = (v) => v == null ? "–" : v > 0 && v < 0.1 ? "<0.1" : Number(v).toFixed(1);
export const fmtProbability = (v) => v == null ? "–" : v > 0 && v < 0.001 ? "<0.1%" : `${(v * 100).toFixed(1)}%`;

export const usd = (v) => (v == null ? "–" : `$${Math.round(v).toLocaleString()}`);

// NWS's own map colors for warnings (weather.gov), so the map reads like the NWS hazard map. Others: gray.
export const NWS_COLORS = {
  "Tornado Warning": "#ff0000", "Tornado Watch": "#ffff00",
  "Severe Thunderstorm Warning": "#ffa500", "Severe Thunderstorm Watch": "#db7093",
  "Flash Flood Warning": "#8b0000", "Flood Warning": "#00ff00", "Flood Advisory": "#00ff7f",
  "Flood Watch": "#2e8b57", "Flash Flood Watch": "#2e8b57", "Coastal Flood Warning": "#228b22",
  "Hurricane Warning": "#dc143c", "Hurricane Watch": "#ff00ff", "Tropical Storm Warning": "#b22222",
  "Tropical Storm Watch": "#f08080", "Storm Surge Warning": "#b524f7",
  "Extreme Heat Warning": "#c71585", "Excessive Heat Warning": "#c71585", "Heat Advisory": "#ff7f50",
  "High Wind Warning": "#daa520", "Wind Advisory": "#d2b48c", "Lake Wind Advisory": "#d2b48c",
  "Red Flag Warning": "#ff1493", "Freeze Warning": "#483d8b", "Frost Advisory": "#6495ed",
  "Dense Fog Advisory": "#708090", "Rip Current Statement": "#40e0d0", "High Surf Advisory": "#ba55d3",
  "Special Weather Statement": "#ffe4b5",
};
export const NWS_OTHER = "#9aa0a6";
export const NWS_COLOR_EXPRESSION = ["match", ["get", "event"], ...Object.entries(NWS_COLORS).flat(), NWS_OTHER];

// Under each AI answer: when it was written and until when it's replayed (next 6 PM), or that it's an offline copy.
export const replayNote = (d) => d.offline ? "Offline: showing the copy saved on this device"
  : `Written ${new Date(d.created_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}`
    + (d.refreshes_at ? ` · replayed until ${new Date(d.refreshes_at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}` : "");
