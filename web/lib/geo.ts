import type { Step } from "@/lib/types";

export const PAIPORTA = { lat: 39.4294, lon: -0.4183 };

export type LonLat = [number, number];

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 6371000;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function lineMetrics(coords: LonLat[]): { cum: number[]; total: number } {
  const cum = [0];
  for (let i = 1; i < coords.length; i += 1) {
    const [lon1, lat1] = coords[i - 1];
    const [lon2, lat2] = coords[i];
    cum.push(cum[i - 1] + haversineM(lat1, lon1, lat2, lon2));
  }
  return { cum, total: cum[cum.length - 1] ?? 0 };
}

function bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export type Pose = { lat: number; lon: number; bearing: number };

export function pointAlong(coords: LonLat[], cum: number[], distance: number): Pose {
  if (coords.length === 0) return { ...PAIPORTA, bearing: 0 };
  if (coords.length === 1 || distance <= 0) {
    const [lon, lat] = coords[0];
    const next = coords[1] ?? coords[0];
    return { lon, lat, bearing: bearing(lat, lon, next[1], next[0]) };
  }
  const total = cum[cum.length - 1] ?? 0;
  const target = Math.min(Math.max(0, distance), total);
  let i = 1;
  while (i < cum.length && cum[i] < target) i += 1;
  const end = Math.min(i, coords.length - 1);
  const start = end - 1;
  const span = (cum[end] ?? 0) - (cum[start] ?? 0) || 1;
  const t = (target - (cum[start] ?? 0)) / span;
  const [lon1, lat1] = coords[start];
  const [lon2, lat2] = coords[end];
  return {
    lon: lon1 + (lon2 - lon1) * t,
    lat: lat1 + (lat2 - lat1) * t,
    bearing: bearing(lat1, lon1, lat2, lon2),
  };
}

function xy(lon: number, lat: number, lat0: number): { x: number; y: number } {
  return {
    x: lon * Math.cos((lat0 * Math.PI) / 180) * 111320,
    y: lat * 110540,
  };
}

export function snapProgress(coords: LonLat[], cum: number[], point: { lat: number; lon: number }): number {
  if (coords.length < 2) return 0;
  const p = xy(point.lon, point.lat, point.lat);
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < coords.length - 1; i += 1) {
    const a = xy(coords[i][0], coords[i][1], point.lat);
    const b = xy(coords[i + 1][0], coords[i + 1][1], point.lat);
    const abx = b.x - a.x;
    const aby = b.y - a.y;
    const len2 = abx * abx + aby * aby || 1;
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * abx + (p.y - a.y) * aby) / len2));
    const dx = a.x + abx * t - p.x;
    const dy = a.y + aby * t - p.y;
    const d = dx * dx + dy * dy;
    if (d < bestD) {
      bestD = d;
      best = (cum[i] ?? 0) + ((cum[i + 1] ?? 0) - (cum[i] ?? 0)) * t;
    }
  }
  return best;
}

export function bannerFor(steps: Step[], progress: number): { instruction: string; distance: number } {
  if (steps.length === 0) return { instruction: "Finding a way out", distance: 0 };
  let current = 0;
  for (let i = 0; i < steps.length; i += 1) {
    if (steps[i].from_m <= progress) current = i;
  }
  const step = steps[current];
  const next = steps[current + 1];
  if (!next) return { instruction: step.instruction, distance: 0 };
  const distance = Math.max(0, next.from_m - progress);
  if (distance < 45) return { instruction: next.instruction, distance };
  if (distance >= 45 && progress > step.from_m + 25 && step.name && !step.arrive) {
    return { instruction: `Continue on ${step.name}`, distance };
  }
  return { instruction: step.instruction, distance };
}

export function formatDistance(meters: number): string {
  const value = Math.max(0, meters);
  if (value >= 1000) return `${(value / 1000).toFixed(1)} km`;
  return `${Math.round(value)} m`;
}

export function insideBbox(
  lat: number,
  lon: number,
  bbox: { west: number; south: number; east: number; north: number },
): boolean {
  return lon >= bbox.west && lon <= bbox.east && lat >= bbox.south && lat <= bbox.north;
}
