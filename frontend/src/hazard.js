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

export const fmtScore = (s) => (s == null ? "–" : Number(s).toFixed(1));

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
  if (s <= 0) return null;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(sec).padStart(2, "0")}`;
}

export const usd = (v) => (v == null ? "–" : `$${Math.round(v).toLocaleString()}`);
