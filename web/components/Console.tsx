"use client";

import { useEffect, useState } from "react";
import { NetworkMap } from "@/components/NetworkMap";
import { DEMO_REPORT, formatEta, type Snapshot } from "@/lib/types";

async function readError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: string };
    return body.detail || text;
  } catch {
    return text || response.statusText;
  }
}

export function Console() {
  const [state, setState] = useState<Snapshot | null>(null);
  const [report, setReport] = useState(DEMO_REPORT);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function call(path: string, init?: RequestInit): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(path, {
        ...init,
        headers: { "content-type": "application/json", ...(init?.headers || {}) },
      });
      if (!response.ok) throw new Error(await readError(response));
      setState((await response.json()) as Snapshot);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/api/state");
        if (!response.ok) throw new Error(await readError(response));
        const data = (await response.json()) as Snapshot;
        if (cancelled) return;
        if (!data.loaded) {
          await call("/api/scenario/reset", { method: "POST" });
          return;
        }
        setState(data);
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : "API unreachable");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // The initial load is intentionally once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const before = state?.routes?.before;
  const after = state?.routes?.after;
  const closed = (state?.closed_roads || []).length > 0;
  const router = state?.router?.chosen ?? "…";
  const cutOff = (state?.districts || []).filter((district) => district.cut_off);

  return (
    <main className="app">
      <header className="top">
        <div>
          <h1>Floodline</h1>
          <p>
            Valencia slice. One citizen report closes the V-31 Paiporta bridge.
            Dispatcher recomputes A-7&apos;s route on the FalkorDB road graph.
            Timing is reconstructed.
          </p>
        </div>
        <div className="badge">
          Router <strong>{router}</strong>
          <div>Tracks 1 + 2</div>
        </div>
      </header>

      <section className="map-wrap">
        <h2>Network</h2>
        {state?.loaded ? (
          <NetworkMap state={state} />
        ) : (
          <p>{busy ? "Loading the road graph…" : "The graph is not loaded yet."}</p>
        )}
        <div className="legend">
          <span><i className="swatch" style={{ background: "#3e5248" }} /> open road</span>
          <span><i className="swatch" style={{ background: "#4b5563" }} /> route before</span>
          <span><i className="swatch" style={{ background: "#157a3a" }} /> route after</span>
          <span><i className="swatch" style={{ background: "#c2410c" }} /> closed</span>
          <span><i className="swatch" style={{ background: "#c026d3" }} /> cut off</span>
        </div>
      </section>

      <section className="panel">
        <h2>Dispatch</h2>
        {error && <div className="error" role="alert">{error}</div>}
        <div className="stats">
          <div><span>Intersections</span>{state?.counts?.intersections ?? "—"}</div>
          <div><span>ROAD edges</span>{state?.counts?.road_edges ?? "—"}</div>
          <div><span>PASSABLE</span>{state?.counts?.passable ?? "—"}</div>
        </div>
        <label htmlFor="report">Citizen report</label>
        <textarea
          id="report"
          value={report}
          onChange={(event) => setReport(event.target.value)}
          disabled={busy}
        />
        <div className="actions">
          <button
            type="button"
            onClick={() => call("/api/scenario/report", { method: "POST", body: JSON.stringify({ text: report }) })}
            disabled={busy || !state?.loaded || closed}
          >
            File flood report
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => call("/api/scenario/corridor", { method: "POST" })}
            disabled={busy || !state?.loaded || cutOff.some((district) => district.name === "Paiporta")}
          >
            Cut off Paiporta
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => call("/api/scenario/reset", { method: "POST" })}
            disabled={busy}
          >
            Reset network
          </button>
        </div>
        {cutOff.length > 0 && (
          <p className="cutoff">
            Cut off: {cutOff.map((district) => district.name).join(", ")}. WCC on PASSABLE found no hospital in that component.
          </p>
        )}
        {state?.corridor && (
          <p>
            maxFlow {state.corridor.before_max_flow} → {state.corridor.after_max_flow} veh/h.
            Critical edge: {state.corridor.critical_edge} ({state.corridor.critical_flow} veh/h).
          </p>
        )}
        <div className="narrative" aria-live="polite">
          {before && (
            <p>
              <strong>Before.</strong> {formatEta(before.travel_s)} via {before.names.join(" → ")} ({before.algo}).
            </p>
          )}
          {after && (
            <p>
              <strong>After.</strong> {formatEta(after.travel_s)} via {after.names.join(" → ")} ({after.algo}).
            </p>
          )}
          {state?.narrative?.closure && <p>{state.narrative.closure}</p>}
          {state?.narrative?.route && <p>{state.narrative.route}</p>}
        </div>
        {state?.decisions && state.decisions.length > 0 && (
          <ol className="path-list">
            {state.decisions.map((decision) => (
              <li key={decision.id}>
                <div>
                  {decision.agent} · {decision.kind}
                  {decision.superseded ? " (superseded)" : ""} · {decision.summary}
                </div>
                {decision.explanation && <p>{decision.explanation}</p>}
              </li>
            ))}
          </ol>
        )}
      </section>

      <footer className="note">
        {state?.network?.source === "osm" ? (
          <>
            Drive network © OpenStreetMap contributors (ODbL). Districts are nearest-town
            centroids, not official boundaries. Hospital beds, shelters, the ambulance, and the
            clock are synthetic. Timing is reconstructed.
          </>
        ) : (
          <>
            Synthetic road network for the October 2024 Valencia DANA story. Not an OpenStreetMap
            extract. Hospital beds, the ambulance, and the report are synthetic. Map geometry is
            drawn from the graph, not from a tile server.
          </>
        )}
      </footer>
    </main>
  );
}
