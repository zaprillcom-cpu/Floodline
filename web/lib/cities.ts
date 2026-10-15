export type CitySlug = "valencia" | "mumbai" | "nyc";

export const BUNDLED_CITIES: { slug: CitySlug; label: string; lat: number; lon: number }[] = [
  { slug: "valencia", label: "Valencia", lat: 39.4294, lon: -0.4183 },
  { slug: "mumbai", label: "Mumbai", lat: 19.076, lon: 72.877 },
  { slug: "nyc", label: "New York", lat: 40.7074, lon: -74.0113 },
];

export function bundledCity(slug: string) {
  return BUNDLED_CITIES.find((city) => city.slug === slug);
}
