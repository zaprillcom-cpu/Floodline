"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { fetchMemoryGraph, type MemoryGraph, type MemoryNode } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { FeedEvent } from "@/lib/types";

const COLORS: Record<string, string> = {
  Report: theme.color.orange,
  Decision: theme.color.blue,
  Task: theme.color.green,
  Agent: theme.color.purple,
  Unit: theme.color.cyan,
  Shelter: "#FFD60A",
  Hospital: theme.color.red,
  Citizen: "#FFFFFF",
  Incident: theme.color.orange,
};

type SimNode = SimulationNodeDatum & MemoryNode;
type SimLink = SimulationLinkDatum<SimNode> & { type: string; derived: boolean };

type Props = {
  graphId: string | null;
  events: FeedEvent[];
  showMap: boolean;
  onShowMap: () => void;
  height?: number;
  focusId?: string | null;
};

export function GraphPanel({ graphId, events, showMap, onShowMap, height = 340, focusId = null }: Props) {
  const [data, setData] = useState<MemoryGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<MemoryNode | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const drawRef = useRef<() => void>(() => {});
  const pickedRef = useRef<MemoryNode | null>(null);
  pickedRef.current = picked;
  useEffect(() => drawRef.current(), [picked]);
  const top = events.reduce((max, event) => Math.max(max, event.seq || 0), 0);

  useEffect(() => {
    let live = true;
    fetchMemoryGraph(graphId)
      .then((next) => {
        if (!live) return;
        setData(next);
        setError(null);
      })
      .catch((exc: unknown) => live && setError(exc instanceof Error ? exc.message : "Graph unavailable"));
    return () => {
      live = false;
    };
  }, [graphId, top]);

  useEffect(() => {
    if (!focusId || !data) return;
    const node = data.nodes.find((item) => item.id === focusId);
    if (node) setPicked(node);
  }, [focusId, data]);

  const sim = useMemo(() => {
    if (!data) return null;
    const nodes: SimNode[] = data.nodes.map((node) => ({ ...node }));
    const links: SimLink[] = data.edges.map((edge) => ({ ...edge }));
    return { nodes, links };
  }, [data]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || !sim) return;
    const dpr = window.devicePixelRatio || 1;
    const width = wrap.clientWidth || 320;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const radius = (node: SimNode) => (node.label === "Agent" ? 9 : node.label === "Decision" ? 6.5 : 5.5);
    const simulation = forceSimulation<SimNode>(sim.nodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(sim.links)
          .id((node) => node.id)
          .distance((link) => (link.type === "MADE" || link.type === "CLAIMED" ? 70 : 42))
          .strength(0.5),
      )
      .force("charge", forceManyBody().strength(-90))
      .force("center", forceCenter(width / 2, height / 2))
      .force("collide", forceCollide<SimNode>().radius((node) => radius(node) + 3));

    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      for (const link of sim.links) {
        const a = link.source as SimNode;
        const b = link.target as SimNode;
        if (a.x == null || b.x == null) continue;
        const duplicate = link.type === "DUPLICATE_OF";
        ctx.strokeStyle = duplicate
          ? "rgba(100,210,255,0.95)"
          : link.type === "SUPERSEDES"
            ? "rgba(255,159,10,0.85)"
            : link.type === "BASED_ON"
              ? "rgba(255,159,10,0.5)"
              : "rgba(255,255,255,0.16)";
        ctx.lineWidth = duplicate ? 2.4 : link.type === "SUPERSEDES" ? 1.8 : 1;
        ctx.setLineDash(duplicate || link.derived ? [5, 4] : []);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y!);
        ctx.lineTo(b.x, b.y!);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const node of sim.nodes) {
        if (node.x == null) continue;
        node.x = Math.max(10, Math.min(width - 10, node.x));
        node.y = Math.max(10, Math.min(height - 10, node.y!));
        const superseded = node.label === "Decision" && node.props.superseded === true;
        ctx.globalAlpha = superseded ? 0.4 : 1;
        ctx.fillStyle = COLORS[node.label] ?? "#888";
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius(node), 0, Math.PI * 2);
        ctx.fill();
        if (pickedRef.current && pickedRef.current.id === node.id) {
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        if (node.label === "Agent") {
          ctx.fillStyle = "rgba(255,255,255,0.85)";
          ctx.font = "600 11px -apple-system, Inter, sans-serif";
          ctx.fillText(node.title, node.x + 12, node.y + 4);
        }
        ctx.globalAlpha = 1;
      }
    };
    drawRef.current = draw;
    simulation.on("tick", draw);

    const pick = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      let best: SimNode | null = null;
      let bestD = 18;
      for (const node of sim.nodes) {
        const d = Math.hypot((node.x ?? 0) - x, (node.y ?? 0) - y);
        if (d < bestD) {
          bestD = d;
          best = node;
        }
      }
      setPicked(best);
    };
    canvas.addEventListener("pointerup", pick);
    return () => {
      simulation.stop();
      canvas.removeEventListener("pointerup", pick);
    };
    // picked is drawn via redraw below; re-running the simulation on pick would reshuffle the layout.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim, height]);

  const edgesOf = picked && data ? data.edges.filter((e) => e.source === picked.id || e.target === picked.id) : [];
  const nameOf = (id: string) => data?.nodes.find((n) => n.id === id);

  return (
    <div className="stack">
      <p className="kicker">Graph</p>
      <h2 className="display tight">Agent memory</h2>
      <p className="lede">
        The Report, Task, and Decision nodes the agents wrote to FalkorDB, with MADE, CLAIMED, BASED_ON, SUPERSEDES,
        and DUPLICATE_OF edges. A duplicate report draws a cyan dashed line to the original. Tap a node.
      </p>
      <button type="button" className={`graph-toggle${showMap ? " on" : ""}`} onClick={onShowMap}>
        {showMap ? "Hide road graph on map" : "Show road graph on map"}
      </button>
      <div ref={wrapRef} className="graph-canvas">
        <canvas ref={canvasRef} aria-label="Agent memory graph" />
        {!data && !error && <p className="empty graph-empty">Reading the graph…</p>}
        {data && data.nodes.length === 0 && <p className="empty graph-empty">No memory yet. File a report.</p>}
      </div>
      {error && <p className="empty">{error}</p>}
      <div className="graph-legend">
        {Object.entries(data?.counts ?? {}).map(([label, count]) => (
          <span key={label}>
            <i style={{ background: COLORS[label] ?? "#888" }} />
            {label} {count}
          </span>
        ))}
      </div>
      {picked && (
        <div className="card graph-detail" id={picked.label === "Report" ? `report-${picked.id}` : undefined}>
          <div className="card-top">
            <span className="agent analyst">{picked.label}</span>
            <span className="when">{picked.id}</span>
          </div>
          <strong className="card-title">{picked.title}</strong>
          <dl className="graph-props">
            {Object.entries(picked.props).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
              </div>
            ))}
          </dl>
          {edgesOf.length > 0 && (
            <ul className="graph-edges">
              {edgesOf.map((edge) => {
                const out = edge.source === picked.id;
                const other = nameOf(out ? edge.target : edge.source);
                return (
                  <li key={`${edge.source}-${edge.type}-${edge.target}`}>
                    {out ? "→" : "←"} <b>{edge.type}</b>
                    {edge.derived ? " (from property)" : ""} {other ? `${other.label}: ${other.title}` : ""}
                  </li>
                );
              })}
            </ul>
          )}
          {picked.cypher && <pre className="query">{picked.cypher}</pre>}
        </div>
      )}
    </div>
  );
}
