export type CitySlug = "valencia" | "mumbai" | "nyc";

export type BundledCity = {
  slug: CitySlug;
  label: string;
  lat: number;
  lon: number;
  west: number;
  south: number;
  east: number;
  north: number;
};

export const BUNDLED_CITIES: BundledCity[] = [
  { slug: "valencia", label: "Valencia", lat: 39.4294, lon: -0.4183, west: -0.5, south: 39.38, east: -0.28, north: 39.55 },
  { slug: "mumbai", label: "Mumbai", lat: 19.1663, lon: 72.8526, west: 72.77, south: 18.89, east: 73.0, north: 19.3 },
  { slug: "nyc", label: "New York", lat: 40.758, lon: -73.9855, west: -74.03, south: 40.698, east: -73.91, north: 40.882 },
];

export function bundledCity(slug: string) {
  return BUNDLED_CITIES.find((city) => city.slug === slug);
}

export function cityContains(city: BundledCity, lat: number, lon: number): boolean {
  return lat >= city.south && lat <= city.north && lon >= city.west && lon <= city.east;
}
