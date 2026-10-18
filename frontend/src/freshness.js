const formatters = new Map();
function localDate(date, timezone) {
  if (!formatters.has(timezone)) formatters.set(timezone, new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }));
  return formatters.get(timezone).format(date);
}

export function scoreIsCurrent(site, horizon, now = new Date()) {
  if (!site.scored_for || !site.timezone) return false;
  if (!Number.isFinite(new Date(site.scored_for).getTime())) return false;
  const localDay = localDate(now, site.timezone);
  const target = new Date(`${localDay}T12:00:00Z`);
  if (horizon === "next_day") target.setUTCDate(target.getUTCDate() + 1);
  const scoreDay = localDate(new Date(site.scored_for), site.timezone);
  return scoreDay === target.toISOString().slice(0, 10);
}

export const SCORE_EXPLANATION = "Daily model: hazard likelihood and severe-outcome likelihood combined on a 0–10 scale. Today's index may be raised by active alerts; it is not a direct probability.";
