export type Disaster = {
  id: string;
  label: string;
  template: (lat: number, lon: number, place?: string) => string;
};

function at(lat: number, lon: number, place?: string): string {
  const where = place && place !== "This point" ? `${place} (${lat.toFixed(5)}, ${lon.toFixed(5)})` : `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  return where;
}

export const DISASTERS: Disaster[] = [
  {
    id: "waterlogging",
    label: "Waterlogging",
    template: (lat, lon, place) => `Waterlogging at ${at(lat, lon, place)}. The road is under water and not passable.`,
  },
  {
    id: "flooded",
    label: "Flooded road",
    template: (lat, lon, place) => `Flooded road at ${at(lat, lon, place)}. Water over the road, not passable.`,
  },
  {
    id: "earthquake",
    label: "Earthquake damage",
    template: (lat, lon, place) => `Earthquake damage at ${at(lat, lon, place)}. The road is closed and not passable.`,
  },
  {
    id: "landslide",
    label: "Landslide",
    template: (lat, lon, place) => `Landslide at ${at(lat, lon, place)}. The road is blocked and not passable.`,
  },
  {
    id: "tree",
    label: "Fallen tree",
    template: (lat, lon, place) => `Fallen tree at ${at(lat, lon, place)}. The road is blocked and not passable.`,
  },
  {
    id: "fire",
    label: "Fire",
    template: (lat, lon, place) => `Fire at ${at(lat, lon, place)}. The road is closed and not passable.`,
  },
  {
    id: "collapse",
    label: "Building collapse",
    template: (lat, lon, place) => `Building collapse at ${at(lat, lon, place)}. The road is blocked and not passable.`,
  },
  {
    id: "power",
    label: "Power line down",
    template: (lat, lon, place) => `Power line down at ${at(lat, lon, place)}. The road is closed and not passable.`,
  },
  {
    id: "blocked",
    label: "Road blocked",
    template: (lat, lon, place) => `Road blocked at ${at(lat, lon, place)}. The road is closed and not passable.`,
  },
];

export function matchingDisasters(text: string): Disaster[] {
  const query = text.trim().toLowerCase();
  if (!query) return [];
  return DISASTERS.filter((item) => item.label.toLowerCase().includes(query) || item.id.includes(query)).slice(0, 6);
}
