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

export function fetchRoute(lat: number, lon: number): Promise<RoutePlan> {
  const query = new URLSearchParams({ lat: String(lat), lon: String(lon) });
  return send<RoutePlan>(`/api/route?${query.toString()}`);
}

export function fetchOverlay(): Promise<Overlay> {
  return send<Overlay>("/api/escape/overlay");
}

export function fetchZones(): Promise<ZonePayload> {
  return send<ZonePayload>("/api/escape/zones");
}

export function fetchFeed(since: number): Promise<{ events: FeedEvent[]; cursor: number }> {
  return send(`/api/feed?since=${since}`);
}

export function fileReport(text: string): Promise<Snapshot> {
  return send<Snapshot>("/api/scenario/report", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function resetNetwork(): Promise<Snapshot> {
  return send<Snapshot>("/api/scenario/reset", { method: "POST" });
}
