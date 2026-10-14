export type RouteView = {
  decision_id: string;
  algo: string;
  travel_s: number;
  osmids: number[];
  names: string[];
  at: string;
};

export type Snapshot = {
  loaded: boolean;
  graph?: string;
  clock?: { opening: string; closure: string; label: string };
  router?: {
    chosen: string | null;
    available: Record<string, { ok?: boolean; travel_s?: number; error?: string }>;
  };
  counts?: { intersections: number; road_edges: number; passable: number };
  nodes: Array<{
    osmid: number;
    name: string;
    lat: number;
    lon: number;
    district: string;
    role: string;
  }>;
  edges: Array<{
    road_id: string;
    name: string;
    from: number;
    to: number;
    travel_s: number;
    base_travel_s: number;
    capacity: number;
    status: string;
  }>;
  hospitals?: Array<{
    id: string;
    name: string;
    beds_free: number;
    beds_total: number;
    er_open: boolean;
    osmid: number;
  }>;
  unit?: { callsign: string; status: string; osmid: number } | null;
  closed_roads?: string[];
  routes?: { before: RouteView | null; after: RouteView | null };
  decisions?: Array<{
    id: string;
    kind: string;
    at: string;
    agent: string | null;
    algo: string;
    explanation: string;
    summary: string;
    superseded: boolean;
  }>;
  narrative?: { closure: string | null; route: string | null };
  load?: { cch?: { ok?: boolean; error?: string }; routers?: { chosen?: string } };
};

export const DEMO_REPORT =
  "Citizen report: the Paiporta bridge on V-31 is under water and not passable.";

export function formatEta(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes === 0) return `${remainder} s`;
  return `${minutes} min ${remainder} s`;
}
