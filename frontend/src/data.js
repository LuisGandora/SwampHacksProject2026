// Map image size (px) used to convert pixel positions into percentages.
export const MAP_SIZE = { width: 850, height: 1100 };

// x/y are pixel positions on public/map.png.
// hazard + info are PLACEHOLDERS: replace with real data / API calls.
// Cities in the central time zone, pretty much everything else should be eastern
const CENTRAL = new Set([
  "Huntsville", "Birmingham", "Tuscaloosa", "Montgomery", "Mobile",
  "Pensacola", "Panama City Beach",
]);

export const CITIES = [
  { name: "Huntsville", x: 196, y: 67 },
  { name: "Birmingham", x: 174, y: 196 },
  { name: "Tuscaloosa", x: 106, y: 228 },
  { name: "Montgomery", x: 218, y: 320 },
  { name: "Mobile", x: 57, y: 496 },
  { name: "Atlanta", x: 390, y: 173 },
  { name: "Columbus", x: 337, y: 311 },
  { name: "Savannah", x: 690, y: 345 },
  { name: "Pensacola", x: 132, y: 526 },
  { name: "Panama City Beach", x: 262, y: 555 },
  { name: "Tallahassee", x: 402, y: 527 },
  { name: "Jacksonville", x: 643, y: 533 },
  { name: "Daytona Beach", x: 706, y: 651 },
  { name: "Orlando", x: 675, y: 725 },
  { name: "Tampa", x: 576, y: 789 },
  { name: "West Palm Beach", x: 806, y: 911 },
  { name: "Fort Lauderdale", x: 799, y: 974 },
  { name: "Miami", x: 796, y: 1011 },
].map((c, i) => ({
  ...c,
  tz: CENTRAL.has(c.name) ? "America/Chicago" : "America/New_York",
  hazard: (i * 7) % 11, // placeholder 0-10
  info: ["General Info 1", "General Info 2", "General Info 3"],
}));

// Convert GPS to a pixel position on map (ref Huntsville + Miami)
export function latLonToPixel(lat, lon) {
  const x = 196 + (lon + 86.59) * 93.75;
  const y = 67 + (34.73 - lat) * 105.2;
  return { x, y };
}