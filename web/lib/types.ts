export type Step = {
  instruction: string;
  name: string;
  distance_m: number;
  from_m: number;
  bearing: number;
  road_ids: string[];
  arrive: boolean;
};

export type Destination = {
  kind: "shelter" | "hospital" | string;
  subtype?: string;
  id: string;
  name: string;
  lat: number | null;
  lon: number | null;
  capacity: number;
  occupancy: number;
  open?: boolean;
  metric?: "people" | "beds" | string;
  osmid?: number;
};

export type Alternative = {
  id: string;
  name: string;
  role: string;
  kind: string;
  lat: number;
  lon: number;
  osmid: number;
  distance_m: number;
  capacity: number;
  occupancy: number;
};

export type RoutePlan = {
  ok: boolean;
  algo: string;
  eta_s: number;
  origin: { osmid: number; name: string; lat: number; lon: number; dist_m?: number };
  destination: Destination;
  distance_m?: number;
  alternatives?: Alternative[];
  geojson: {
    type: "Feature";
    geometry: { type: "LineString"; coordinates: [number, number][] };
    properties?: { algo?: string; eta_s?: number };
  };
  steps: Step[];
};

export type FeedEvent = {
  type: string;
  id?: string;
  seq: number;
  at?: string;
  agent: string;
  kind?: string;
  status?: string;
  title: string;
  body: string;
  query?: string;
  travel_s?: number | null;
  road_id?: string | null;
  algo?: string;
  planner?: string;
  reasoning?: string;
};

export type Pin = {
  kind: string;
  id: string;
  name: string;
  lat: number;
  lon: number;
  capacity: number;
  occupancy: number;
  osmid: number;
};

export type ClosedRoad = {
  road_id: string;
  name: string;
  coordinates: [number, number][];
  report?: string;
  closure_type?: string;
  at?: string;
  closed_at?: string;
  agent?: string;
  decision_id?: string;
  closed_by?: string;
};

export type Overlay = {
  loaded: boolean;
  graph?: string;
  bbox: { west: number; south: number; east: number; north: number };
  counts: { intersections: number; road_edges: number; passable: number } | null;
  network: { source: string; attribution: string } | null;
  router: string | null;
  pins: Pin[];
  closed: ClosedRoad[];
  districts?: { name: string; cut_off: boolean }[];
};

export type ZonePayload = {
  algo?: string;
  zones: Array<{ name: string; population?: number; polygon: [number, number][] }>;
};

export type TabId = "escape" | "report" | "live" | "graph" | "me";

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
  rejected?: boolean;
  action?: string;
  message?: string;
  graph?: string;
  clock?: { opening: string; closure: string; label: string };
  router?: {
    chosen: string | null;
    available: Record<string, { ok?: boolean; travel_s?: number; error?: string }>;
  };
  network?: {
    source: string;
    bbox?: { west: number; south: number; east: number; north: number } | null;
    attribution?: string;
    district_method?: string;
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
  districts?: Array<{
    name: string;
    population: number;
    cut_off: boolean;
    component_id: number | null;
  }>;
  corridor?: {
    district: string;
    before_max_flow: number;
    after_max_flow: number;
    critical_edge: string | null;
    critical_flow: number | null;
    closed: string[];
  };
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
    seq?: number;
    kind: string;
    at: string;
    agent: string | null;
    algo: string;
    explanation: string;
    summary: string;
    superseded: boolean;
    planner?: string;
    reasoning?: string;
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
