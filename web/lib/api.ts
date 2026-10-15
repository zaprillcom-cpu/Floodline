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
  place?: string;
  center?: { lat: number; lon: number };
};

function withGraph(path: string, graph?: string | null): string {
  if (!graph) return path;
  const join = path.includes("?") ? "&" : "?";
  return `${path}${join}graph=${encodeURIComponent(graph)}`;
}

export function fetchRoute(
  lat: number,
  lon: number,
  graph?: string | null,
  dest?: string | null,
): Promise<RoutePlan> {
  const query = new URLSearchParams({ lat: String(lat), lon: String(lon) });
  if (graph) query.set("graph", graph);
  if (dest) query.set("dest", dest);
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

export type AreaJob = AreaLoad & {
  job_id: string;
  status: "queued" | "fetching" | "indexing" | "ready" | "failed" | string;
  message: string;
};

const AREA_POLL_MS = 1500;
const AREA_STALL_MS = 45_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function fetchAreaStatus(jobId: string): Promise<AreaJob> {
  return send<AreaJob>(`/api/area/status?job_id=${encodeURIComponent(jobId)}`);
}

export async function loadArea(
  lat: number,
  lon: number,
  radiusM?: number,
  onUpdate?: (job: AreaJob) => void,
  shouldStop?: () => boolean,
): Promise<AreaLoad> {
  const body: { lat: number; lon: number; radius_m?: number } = { lat, lon };
  if (radiusM != null) body.radius_m = radiusM;
  const queued = await send<AreaJob>("/api/area/load", {
    method: "POST",
    body: JSON.stringify(body),
  });
  onUpdate?.(queued);
  let marker = `${queued.status}|${queued.message}`;
  let changedAt = Date.now();
  let current = queued;
  while (!shouldStop?.()) {
    if (current.status === "ready" && current.graph) return current;
    if (current.status === "failed") {
      throw new Error(
        current.message ||
          "Overpass could not download streets for this area. Choose a bundled city, which uses an offline extract.",
      );
    }
    if (Date.now() - changedAt >= AREA_STALL_MS) {
      throw new Error("Street mapping stalled. Choose a bundled city, which uses an offline extract.");
    }
    await sleep(AREA_POLL_MS);
    if (shouldStop?.()) break;
    current = await fetchAreaStatus(queued.job_id);
    const next = `${current.status}|${current.message}`;
    if (next !== marker) {
      marker = next;
      changedAt = Date.now();
    }
    onUpdate?.(current);
  }
  throw new Error("cancelled");
}

export function loadCity(slug: string): Promise<AreaLoad> {
  return send<AreaLoad>(`/api/area/city/${encodeURIComponent(slug)}`, { method: "POST" });
}

export function loadValenciaDemo(): Promise<AreaLoad> {
  return loadCity("valencia");
}

export function resetNetwork(): Promise<Snapshot> {
  return send<Snapshot>("/api/scenario/reset", { method: "POST" });
}
