import type { FeedEvent, Overlay, RoutePlan, Snapshot, ZonePayload } from "@/lib/types";

async function readError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string };
    return body.detail || text;
  } catch {
    return text || response.statusText;
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as T;
}

export type AreaLoad = {
  ok: boolean;
  graph: string;
  cached: boolean;
  origin: string;
  intersections?: number;
  road_edges?: number;
  passable?: number;
  shelters?: number;
  bbox?: { west: number; south: number; east: number; north: number };
};

function withGraph(path: string, graph?: string | null): string {
  if (!graph) return path;
  const join = path.includes("?") ? "&" : "?";
  return `${path}${join}graph=${encodeURIComponent(graph)}`;
}

export function fetchRoute(lat: number, lon: number, graph?: string | null): Promise<RoutePlan> {
  const query = new URLSearchParams({ lat: String(lat), lon: String(lon) });
  if (graph) query.set("graph", graph);
  return send<RoutePlan>(`/api/route?${query.toString()}`);
}

export function fetchOverlay(graph?: string | null): Promise<Overlay> {
  return send<Overlay>(withGraph("/api/escape/overlay", graph));
}

export function fetchZones(graph?: string | null): Promise<ZonePayload> {
  return send<ZonePayload>(withGraph("/api/escape/zones", graph));
}

export function fetchFeed(since: number, graph?: string | null): Promise<{ events: FeedEvent[]; cursor: number }> {
  return send(withGraph(`/api/feed?since=${since}`, graph));
}

export function fileReport(text: string, graph?: string | null): Promise<Snapshot> {
  return send<Snapshot>("/api/scenario/report", {
    method: "POST",
    body: JSON.stringify({ text, graph: graph || undefined }),
  });
}

export function loadArea(lat: number, lon: number, radiusM = 3000): Promise<AreaLoad> {
  return send<AreaLoad>("/api/area/load", {
    method: "POST",
    body: JSON.stringify({ lat, lon, radius_m: radiusM }),
  });
}

export function loadValenciaDemo(): Promise<AreaLoad> {
  return send<AreaLoad>("/api/area/valencia", { method: "POST" });
}

export function resetNetwork(): Promise<Snapshot> {
  return send<Snapshot>("/api/scenario/reset", { method: "POST" });
}
