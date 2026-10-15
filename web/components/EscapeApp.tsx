"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { DesktopHud } from "@/components/DesktopHud";
import { MobileShell } from "@/components/MobileShell";
import { fetchFeed, fetchOverlay, fetchRoute, fetchZones, fileReport, resetNetwork } from "@/lib/api";
import {
  PAIPORTA,
  bannerFor,
  formatDistance,
  insideBbox,
  lineMetrics,
  pointAlong,
  snapProgress,
  type Pose,
} from "@/lib/geo";
import type { SavedPlace } from "@/lib/storage";
import type { FeedEvent, Overlay, RoutePlan, TabId } from "@/lib/types";

const FloodMap = dynamic(() => import("@/components/FloodMap").then((mod) => mod.FloodMap), { ssr: false });

type Notice = { title: string; detail: string } | null;

function eventKey(event: FeedEvent): string {
  return `${event.type}:${event.id ?? event.title}:${event.seq}`;
}

export function EscapeApp() {
  const [width, setWidth] = useState(() => window.innerWidth);
  const [overlay, setOverlay] = useState<Overlay | null>(null);
  const [route, setRoute] = useState<RoutePlan | null>(null);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [zones, setZones] = useState<Array<{ name: string; polygon: [number, number][] }>>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [flood, setFlood] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [report, setReport] = useState("Water over the road ahead. Flooded and not passable.");
  const [tab, setTab] = useState<TabId>("escape");
  const [progress, setProgress] = useState(0);
  const [pose, setPose] = useState<Pose>({ ...PAIPORTA, bearing: 0 });
  const progressRef = useRef(0);
  const poseRef = useRef(pose);
  const seen = useRef(new Set<string>());
  const liveAfter = useRef(0);
  const armed = useRef(false);
  const routeRef = useRef<RoutePlan | null>(null);
  const rerouteRef = useRef<(lat: number, lon: number) => void>(() => {});
  poseRef.current = pose;
  routeRef.current = route;

  const mobile = width < 768;
  const coords = route?.geojson.geometry.coordinates ?? null;
  const metrics = useMemo(() => lineMetrics(coords ?? []), [coords]);
  const banner = route ? bannerFor(route.steps, progress) : null;

  const remember = useCallback((incoming: FeedEvent[], live: boolean) => {
    setEvents((current) => {
      const next = [...current];
      for (const event of incoming) {
        if (event.type === "heartbeat") continue;
        const key = eventKey(event);
        if (seen.current.has(key)) continue;
        seen.current.add(key);
        next.push(event);
      }
      next.sort((a, b) => b.seq - a.seq || (a.type < b.type ? 1 : -1));
      return next.slice(0, 40);
    });
    if (!live || !armed.current) return;
    const interesting = incoming.filter(
      (event) => event.seq > liveAfter.current && (event.type === "route" || event.kind === "close"),
    );
    if (interesting.length === 0) return;
    liveAfter.current = Math.max(liveAfter.current, ...interesting.map((event) => event.seq));
    const here = poseRef.current;
    rerouteRef.current(here.lat, here.lon);
  }, []);

  async function refreshRoute(lat: number, lon: number, reason: "load" | "reroute" | "move") {
    const previous = routeRef.current;
    const plan = await fetchRoute(lat, lon);
    const nextCoords = plan.geojson.geometry.coordinates;
    const nextMetrics = lineMetrics(nextCoords);
    if (reason === "reroute" && previous) {
      const previousTotal = Math.max(lineMetrics(previous.geojson.geometry.coordinates).total, 1);
      const remainBefore = previous.eta_s * (1 - progressRef.current / previousTotal);
      progressRef.current = snapProgress(nextCoords, nextMetrics.cum, poseRef.current);
      const delta = Math.round(plan.eta_s - remainBefore);
      const sign = delta >= 0 ? "+" : "−";
      setNotice({
        title: `Rerouted · ${sign}${Math.abs(delta)} s`,
        detail: "A closed road dropped out of the FalkorDB path. The line is the new way out.",
      });
    } else if (reason !== "reroute") {
      progressRef.current = 0;
      setProgress(0);
      const start = pointAlong(nextCoords, nextMetrics.cum, 0);
      poseRef.current = { lat, lon, bearing: start.bearing };
      setPose({ lat, lon, bearing: start.bearing });
    }
    setRoute(plan);
    setProgress(progressRef.current);
  }

  rerouteRef.current = (lat: number, lon: number) => {
    void refreshRoute(lat, lon, "reroute").catch((exc: unknown) => {
      setError(exc instanceof Error ? exc.message : "Could not reroute");
    });
  };

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [map, feed] = await Promise.all([fetchOverlay(), fetchFeed(0)]);
        if (cancelled) return;
        setOverlay(map);
        remember(feed.events, false);
        liveAfter.current = feed.cursor;
        armed.current = true;
        if (map.loaded) {
          await refreshRoute(PAIPORTA.lat, PAIPORTA.lon, "load");
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "API unreachable");
      }
    })();
    return () => {
      cancelled = true;
    };
    // Initial load only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let poll: number | null = null;
    const source = new EventSource("/api/stream");
    const take = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as FeedEvent;
        remember([data], true);
      } catch {
        /* ignore a partial chunk */
      }
    };
    source.addEventListener("decision", take);
    source.addEventListener("task", take);
    source.addEventListener("route", take);
    source.onopen = () => {
      if (poll != null) {
        window.clearInterval(poll);
        poll = null;
      }
    };
    source.onerror = () => {
      if (poll != null) return;
      poll = window.setInterval(() => {
        void fetchFeed(liveAfter.current)
          .then((body) => {
            liveAfter.current = Math.max(liveAfter.current, body.cursor);
            remember(body.events, true);
          })
          .catch(() => undefined);
      }, 2000);
    };
    return () => {
      source.close();
      if (poll != null) window.clearInterval(poll);
    };
  }, [remember]);

  const onSample = useCallback((next: number, sample: Pose) => {
    progressRef.current = next;
    poseRef.current = sample;
    setProgress(next);
    setPose(sample);
  }, []);

  async function locate() {
    const bbox = overlay?.bbox;
    if (!navigator.geolocation || !bbox) {
      setError("Location is unavailable. Staying in Paiporta.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = position.coords.latitude;
        const lon = position.coords.longitude;
        if (!insideBbox(lat, lon, bbox)) {
          setError("That fix is outside the Valencia corridor. Staying on the simulated position.");
          return;
        }
        setError(null);
        setNavigating(false);
        void refreshRoute(lat, lon, "move").catch((exc: unknown) =>
          setError(exc instanceof Error ? exc.message : "Could not route"),
        );
      },
      () => setError("Location was denied. Staying on the simulated position."),
      { enableHighAccuracy: true, timeout: 8000 },
    );
  }

  async function submit(text: string) {
    setBusy(true);
    setError(null);
    try {
      const state = await fileReport(text);
      const mapped: FeedEvent[] = (state.decisions || []).map((decision) => ({
        type: decision.kind === "route" ? "route" : "decision",
        id: decision.id,
        seq: decision.seq ?? 0,
        at: decision.at,
        agent: decision.agent || (decision.kind === "close" ? "Scout" : decision.kind === "route" ? "Dispatcher" : "Analyst"),
        kind: decision.kind,
        title: decision.summary || decision.kind,
        body: decision.explanation,
        query: decision.algo || "",
        algo: decision.algo,
      }));
      remember(mapped, false);
      const nextOverlay = await fetchOverlay();
      setOverlay(nextOverlay);
      const here = poseRef.current;
      const before = routeRef.current;
      const remainBefore = before
        ? before.eta_s * (1 - progressRef.current / Math.max(lineMetrics(before.geojson.geometry.coordinates).total, 1))
        : 0;
      const plan = await fetchRoute(here.lat, here.lon);
      const nextCoords = plan.geojson.geometry.coordinates;
      const nextMetrics = lineMetrics(nextCoords);
      progressRef.current = snapProgress(nextCoords, nextMetrics.cum, here);
      setRoute(plan);
      setProgress(progressRef.current);
      const closedName = nextOverlay.closed[0]?.name || "the road ahead";
      const delta = Math.round(plan.eta_s - remainBefore);
      const sign = delta > 0 ? "+" : delta < 0 ? "−" : "+";
      setNotice({
        title: `Rerouted · ${closedName}`,
        detail: `${sign}${Math.abs(delta)} s. ${state.narrative?.closure || "The closed edge is out of the path."}`,
      });
      if (flood) {
        const shaded = await fetchZones();
        setZones(shaded.zones);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Report failed");
    } finally {
      setBusy(false);
    }
  }

  function aheadText(): string {
    const point = coords && coords.length >= 2
      ? pointAlong(coords, metrics.cum, Math.min(metrics.total, progressRef.current + 140))
      : poseRef.current;
    return `Road flooded and not passable at ${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`;
  }

  async function toggleFlood() {
    const next = !flood;
    setFlood(next);
    if (!next) return;
    try {
      const shaded = await fetchZones();
      setZones(shaded.zones);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Cut-off zones are unavailable");
    }
  }

  async function loadDemo() {
    setBusy(true);
    setError(null);
    try {
      await resetNetwork();
      const map = await fetchOverlay();
      setOverlay(map);
      await refreshRoute(PAIPORTA.lat, PAIPORTA.lon, "load");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Demo load failed");
    } finally {
      setBusy(false);
    }
  }

  function goToPlace(place: SavedPlace) {
    setNavigating(false);
    setTab("escape");
    void refreshRoute(place.lat, place.lon, "move").catch((exc: unknown) =>
      setError(exc instanceof Error ? exc.message : "Could not route"),
    );
  }

  const shared = {
    route,
    progress,
    total: metrics.total,
    navigating,
    busy,
    error,
    overlay,
    flood,
    notice,
    events,
    report,
    pose,
    onReport: setReport,
    onSubmit: () => void submit(report),
    onAhead: () => void submit(aheadText()),
    onStart: () => {
      if (!route) return;
      setNavigating(true);
    },
    onStop: () => setNavigating(false),
    onLocate: () => void locate(),
    onFlood: () => void toggleFlood(),
    onRetry: () => void refreshRoute(pose.lat, pose.lon, "load").catch((exc: unknown) => setError(String(exc))),
    onDemo: () => void loadDemo(),
    onGo: goToPlace,
  };

  return (
    <div className={`app-root${flood ? " flood" : ""}${mobile ? " mobile" : ""}`}>
      <div className="scrim" />
      <div className="flood-veil" />
      <FloodMap
        center={pose}
        routeCoords={coords}
        routeKey={route ? `${route.destination.id}:${route.eta_s}:${coords?.[0]?.join(",")}` : "none"}
        closed={overlay?.closed ?? []}
        zones={zones}
        pins={overlay?.pins ?? []}
        activePinId={route?.destination.id ?? null}
        flood={flood}
        navigating={navigating}
        progressRef={progressRef}
        paddingRight={mobile ? 12 : width >= 1024 ? 400 : 340}
        mobile={mobile}
        onSample={onSample}
      />
      <header className="float-brand">
        <span>Floodline</span>
        <strong>Valencia</strong>
      </header>
      {flood && <div className="vision-chip">Flood vision</div>}
      {banner && (
        <div className="banner" aria-live="polite">
          <strong>{banner.instruction}</strong>
          <span>{formatDistance(banner.distance)}</span>
        </div>
      )}
      {mobile ? <MobileShell tab={tab} onTab={setTab} {...shared} /> : <DesktopHud {...shared} />}
      <p className="attrib">
        Streets © OpenStreetMap · OpenFreeMap · {overlay?.network?.attribution || "Graph © OpenStreetMap contributors"}
      </p>
    </div>
  );
}
