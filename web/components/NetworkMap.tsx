import type { Snapshot } from "@/lib/types";

type Point = { x: number; y: number };

const WIDTH = 800;
const HEIGHT = 640;
const PAD = 56;

function project(nodes: Snapshot["nodes"]): Map<number, Point> {
  const lats = nodes.map((node) => node.lat);
  const lons = nodes.map((node) => node.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const points = new Map<number, Point>();
  for (const node of nodes) {
    const x = PAD + ((node.lon - minLon) / (maxLon - minLon || 1)) * (WIDTH - PAD * 2);
    const y = PAD + ((maxLat - node.lat) / (maxLat - minLat || 1)) * (HEIGHT - PAD * 2);
    points.set(node.osmid, { x, y });
  }
  return points;
}

function hops(osmids: number[] | undefined): Set<string> {
  const keys = new Set<string>();
  if (!osmids) return keys;
  for (let i = 0; i < osmids.length - 1; i += 1) {
    const a = osmids[i];
    const b = osmids[i + 1];
    keys.add(a < b ? `${a}-${b}` : `${b}-${a}`);
  }
  return keys;
}

function line(osmids: number[], points: Map<number, Point>): string {
  return osmids
    .map((osmid) => {
      const point = points.get(osmid);
      return point ? `${point.x},${point.y}` : "";
    })
    .filter(Boolean)
    .join(" ");
}

export function NetworkMap({ state }: { state: Snapshot }) {
  const points = project(state.nodes);
  const seen = new Set<string>();
  const roads = state.edges.filter((edge) => {
    if (seen.has(edge.road_id)) return false;
    seen.add(edge.road_id);
    return true;
  });
  const before = state.routes?.before;
  const after = state.routes?.after;
  const closed = roads.filter((edge) => edge.status === "closed");
  const dense = state.nodes.length > 40;
  const osm = state.network?.source === "osm";
  const cutOff = new Set((state.districts || []).filter((item) => item.cut_off).map((item) => item.name));
  const title = osm ? "L'Horta Sud — OpenStreetMap drive network" : "L'Horta Sud — synthetic network";

  const summary = after
    ? `Old route ${before?.names.join(" to ")}, new route ${after.names.join(" to ")}.`
    : `Current route ${before?.names.join(" to ") ?? "not loaded"}.`;

  return (
    <svg className="map" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={summary}>
      <text x={PAD} y={28} fill="#1a2a22" fontSize="18" fontFamily="Palatino, Georgia, serif">
        {title}
      </text>
      {roads.map((edge) => {
        const a = points.get(edge.from);
        const b = points.get(edge.to);
        if (!a || !b) return null;
        const isClosed = edge.status === "closed";
        return (
          <line
            key={edge.road_id}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke={isClosed ? "#c2410c" : "#3e5248"}
            strokeWidth={isClosed ? (dense ? 3 : 7) : dense ? 1 : 3}
            strokeLinecap="round"
          />
        );
      })}
      {before && (
        <polyline
          points={line(before.osmids, points)}
          fill="none"
          stroke="#4b5563"
          strokeWidth={5}
          strokeDasharray="8 7"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {after && (
        <polyline
          points={line(after.osmids, points)}
          fill="none"
          stroke="#157a3a"
          strokeWidth={6}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {closed.slice(0, 8).map((edge) => {
        const a = points.get(edge.from);
        const b = points.get(edge.to);
        if (!a || !b) return null;
        return (
          <g key={`${edge.road_id}-label`}>
            <text
              x={(a.x + b.x) / 2}
              y={(a.y + b.y) / 2 - 10}
              textAnchor="middle"
              fill="#9a3412"
              fontSize="13"
              fontWeight="700"
            >
              CLOSED
            </text>
          </g>
        );
      })}
      {state.nodes.map((node) => {
        const point = points.get(node.osmid);
        if (!point) return null;
        const unitHere = state.unit?.osmid === node.osmid;
        const isolated = cutOff.has(node.district);
        const labeled = !dense || node.role !== "junction" || unitHere || isolated;
        if (dense && node.role === "junction" && !unitHere && !isolated) return null;
        return (
          <g key={node.osmid}>
            {isolated && (
              <circle cx={point.x} cy={point.y} r={16} fill="#c026d3" opacity={0.35} />
            )}
            <circle
              cx={point.x}
              cy={point.y}
              r={node.role === "junction" ? 5 : 8}
              fill={isolated ? "#86198f" : node.role === "hospital" ? "#1d4ed8" : node.role === "shelter" ? "#7c3aed" : "#1a2a22"}
              stroke="#f8faf5"
              strokeWidth="2"
            />
            {labeled && (
              <text x={point.x + 10} y={point.y - 8} fill="#1a2a22" fontSize="13">
                {unitHere ? `${state.unit?.callsign} · ${node.name}` : node.name}
              </text>
            )}
          </g>
        );
      })}
      <text x={PAD} y={HEIGHT - 18} fill="#3e5248" fontSize="12">
        Grey dash: route before the closure. Green: route after. Red: closed segment.
      </text>
      <desc>{`Highlighted hops before ${[...hops(before?.osmids)].join(",")}`}</desc>
    </svg>
  );
}
