const R_MILES = 3958.8;

export function milesBetween(lat1, lon1, lat2, lon2) {
  const rad = (d) => (d * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R_MILES * Math.asin(Math.sqrt(a));
}

const latOf = (s) => s.lat ?? s.latitude;
const lonOf = (s) => s.lon ?? s.lng ?? s.longitude;

export function sitesWithin(sites, focus) {
  if (!focus || focus.all) return sites;
  return sites.filter(
    (s) => milesBetween(focus.lat, focus.lon, latOf(s), lonOf(s)) <= focus.radius
  );
}