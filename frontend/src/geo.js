const R_MILES = 3958.8;

export function milesBetween(lat1, lon1, lat2, lon2) {
  const rad = (d) => (d * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R_MILES * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
}

const latOf = (s) => s.lat ?? s.latitude;
const lonOf = (s) => s.lon ?? s.lng ?? s.longitude;

export function sitesWithin(sites, focus) {
  if (!focus || focus.all) return sites;
  return sites.filter(
    (s) => milesBetween(focus.lat, focus.lon, latOf(s), lonOf(s)) <= focus.radius
  );
}

function validPoint(point) {
  return point && Number.isFinite(point.lat) && Math.abs(point.lat) <= 90
    && Number.isFinite(point.lon) && Math.abs(point.lon) <= 180;
}

// The live feed's coordinates are the affected site's location, not the hazard's origin.
export function nearbyAlerts(alerts, location, now = Date.now(), radius = 10) {
  if (!validPoint(location)) return [];
  return alerts.flatMap((alert) => {
    if (!validPoint(alert) || alert.cleared_at) return [];
    if (alert.all_clear_at && !(new Date(alert.all_clear_at).getTime() > Number(now))) return [];
    const distance_mi = milesBetween(location.lat, location.lon, alert.lat, alert.lon);
    return distance_mi <= radius ? [{ ...alert, distance_mi }] : [];
  }).sort((a, b) => a.distance_mi - b.distance_mi
    || Number(b.level === "stop_work") - Number(a.level === "stop_work"));
}
