"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { DesktopHud } from "@/components/DesktopHud";
import { MobileShell } from "@/components/MobileShell";
import { fetchNetworkGraph, fetchFeed, fetchOverlay, fetchRoute, fetchZones, fileReport, loadArea, reopenRoad, type AreaJob } from "@/lib/api";
import { MapPrompt } from "@/components/MapPrompt";
import { DISASTERS } from "@/lib/disasters";
import { bundledCity, cityContains, type CitySlug } from "@/lib/cities";
import {
  PAIPORTA,
  bannerFor,
  formatDistance,
  lineMetrics,
  pointAlong,
  snapProgress,
  type Pose,
} from "@/lib/geo";
import type { SavedPlace } from "@/lib/storage";
import type { ClosedRoad, FeedEvent, Overlay, RoutePlan, TabId } from "@/lib/types";

import type { GraphNet } from "@/components/FloodMap";

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
  const [graphId, setGraphId] = useState<string | null>(null);
  const [phase, setPhase] = useState<"boot" | "mapping" | "ready" | "choose">("boot");
  const [mapProgress, setMapProgress] = useState(4);
  const [place, setPlace] = useState("Nearby");
  const [mappingTitle, setMappingTitle] = useState("Mapping streets around you…");
  const [stageText, setStageText] = useState("Finding your position");
  const [chooser, setChooser] = useState(0);
  const [reportFocus, setReportFocus] = useState(0);
  const [travel, setTravel] = useState<"live" | "demo">("demo");
  const [promptPlace, setPromptPlace] = useState<{ lat: number; lon: number; name: string; pinId?: string } | null>(null);
  const [promptClosure, setPromptClosure] = useState<ClosedRoad | null>(null);
  const [promptArea, setPromptArea] = useState<{ lat: number; lon: number; name: string } | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [graphNet, setGraphNet] = useState<GraphNet | null>(null);
  const progressRef = useRef(0);
  const poseRef = useRef(pose);
  const seen = useRef(new Set<string>());
  const liveAfter = useRef(0);
  const armed = useRef(false);
  const routeRef = useRef<RoutePlan | null>(null);
  const graphRef = useRef<string | null>(null);
  const modeRef = useRef<"local" | CitySlug | null>(null);
  const loadGen = useRef(0);
  const progressCap = useRef(18);
  const lastFix = useRef<{ lat: number; lon: number } | null>(null);
  const destRef = useRef<string | null>(null);
  const navigatingRef = useRef(false);
  navigatingRef.current = navigating;
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

  function adoptGraph(id: string) {
    if (graphRef.current && graphRef.current !== id) {
      seen.current = new Set();
      setEvents([]);
      setRoute(null);
      armed.current = false;
    }
    graphRef.current = id;
    setGraphId(id);
  }

  async function refreshRoute(
    lat: number,
    lon: number,
    reason: "load" | "reroute" | "move",
    graph?: string | null,
    dest?: string | null,
  ) {
    const previous = routeRef.current;
    const chosen = dest === undefined ? destRef.current : dest;
    const plan = await fetchRoute(lat, lon, graph ?? graphRef.current, chosen);
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
    if (phase !== "mapping") return;
    const id = window.setInterval(() => {
      setMapProgress((value) => {
        const cap = progressCap.current;
        if (value >= cap) return value;
        return Math.min(cap, value + Math.max(0.4, (cap - value) * 0.08));
      });
    }, 140);
    return () => window.clearInterval(id);
  }, [phase]);

  useEffect(() => {
    if (!graphId) return;
    let poll: number | null = null;
    const source = new EventSource(`/api/stream?graph=${encodeURIComponent(graphId)}`);
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
        void fetchFeed(liveAfter.current, graphId)
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
  }, [graphId, remember]);

  const onSample = useCallback((next: number, sample: Pose) => {
    progressRef.current = next;
    poseRef.current = sample;
    setProgress(next);
    setPose(sample);
  }, []);

  function noteArea(job: AreaJob) {
    const caps: Record<string, number> = { queued: 22, fetching: 55, indexing: 84, ready: 100, failed: 8 };
    progressCap.current = caps[job.status] ?? 30;
    if (job.message) setStageText(job.message);
    if (job.status === "ready") setMapProgress(100);
  }

  async function loadAround(lat: number, lon: number) {
    const gen = ++loadGen.current;
    modeRef.current = "local";
    setPlace("Nearby");
    setMappingTitle("Mapping streets around you…");
    setStageText("Finding your position");
    progressCap.current = 18;
    setPhase("mapping");
    setMapProgress(8);
    setError(null);
    setNavigating(false);
    const here = { lat, lon, bearing: poseRef.current.bearing };
    poseRef.current = here;
    setPose(here);
    try {
      lastFix.current = { lat, lon };
      setTravel("live");
      destRef.current = null;
      const area = await loadArea(lat, lon, 2500, noteArea, () => loadGen.current !== gen);
      if (loadGen.current !== gen) return;
      if (area.place) setPlace(area.place);
      adoptGraph(area.graph);
      const [map, feed] = await Promise.all([fetchOverlay(area.graph), fetchFeed(0, area.graph)]);
      setOverlay(map);
      remember(feed.events, false);
      liveAfter.current = feed.cursor;
      armed.current = true;
      await refreshRoute(lat, lon, "load", area.graph);
      setMapProgress(100);
      setPhase("ready");
    } catch (exc) {
      if (loadGen.current !== gen) return;
      revealCities();
      const message = exc instanceof Error ? exc.message : "Could not map this area";
      setError(
        /bundled city/i.test(message) ? message : `${message} Choose a bundled city below.`,
      );
    }
  }

  function revealCities() {
    setPhase("choose");
    setMapProgress(0);
    setTab("escape");
    setChooser((value) => value + 1);
  }

  function locate() {
    const geo = navigator.geolocation;
    if (!geo) {
      revealCities();
      setError("This browser has no location. Choose a bundled city below.");
      return;
    }
    setMappingTitle("Mapping streets around you…");
    setPhase("mapping");
    setMapProgress(8);
    setError(null);
    geo.getCurrentPosition(
      (position) => {
        void loadAround(position.coords.latitude, position.coords.longitude);
      },
      () => {
        revealCities();
        setError("Location was denied. Choose a bundled city below.");
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 15000 },
    );
  }

  async function submit(text: string) {
    setBusy(true);
    setError(null);
    try {
      const active = graphRef.current;
      const state = await fileReport(text, active);
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
      const nextOverlay = await fetchOverlay(active);
      setOverlay(nextOverlay);
      const here = poseRef.current;
      const before = routeRef.current;
      const remainBefore = before
        ? before.eta_s * (1 - progressRef.current / Math.max(lineMetrics(before.geojson.geometry.coordinates).total, 1))
        : 0;
      const plan = await fetchRoute(here.lat, here.lon, active);
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
        const shaded = await fetchZones(graphRef.current);
        setZones(shaded.zones);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Report failed");
    } finally {
      setBusy(false);
    }
  }

  async function refreshAfterReopen() {
    const active = graphRef.current;
    const nextOverlay = await fetchOverlay(active);
    setOverlay(nextOverlay);
    const here = poseRef.current;
    await refreshRoute(here.lat, here.lon, "move", active);
  }

  async function markClear(roadId: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await reopenRoad({ road_id: roadId, graph: graphRef.current });
      await refreshAfterReopen();
      setPromptClosure(null);
      setNotice({ title: "Road is open", detail: result.message || "The closure was cleared and the route was recomputed." });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not reopen that road");
    } finally {
      setBusy(false);
    }
  }

  async function markAreaOk(lat: number, lon: number) {
    setBusy(true);
    setError(null);
    try {
      const result = await reopenRoad({ lat, lon, graph: graphRef.current });
      await refreshAfterReopen();
      setPromptArea(null);
      setNotice({ title: "Your area is OK", detail: result.message || "Nearby closures were cleared." });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not clear this area");
    } finally {
      setBusy(false);
    }
  }

  async function clearClosures() {
    setBusy(true);
    setError(null);
    try {
      const result = await reopenRoad({ clear_test: true, graph: graphRef.current });
      await refreshAfterReopen();
      setNotice({ title: "Test closures cleared", detail: result.message || "Closed roads on this map were reopened." });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not clear closures");
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
      const shaded = await fetchZones(graphRef.current);
      setZones(shaded.zones);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Cut-off zones are unavailable");
    }
  }

  async function openCity(slug: CitySlug) {
    const city = bundledCity(slug);
    if (!city) return;
    const gen = ++loadGen.current;
    modeRef.current = slug;
    const fix = lastFix.current;
    const useFix = fix != null && cityContains(city, fix.lat, fix.lon);
    const lat = useFix ? fix.lat : city.lat;
    const lon = useFix ? fix.lon : city.lon;
    setTravel(useFix ? "live" : "demo");
    destRef.current = null;
    setPlace(city.label);
    setMappingTitle(`Loading ${city.label}…`);
    setStageText(useFix ? "Reading bundled streets around you" : `Reading bundled streets in ${city.label}`);
    progressCap.current = 70;
    setPhase("mapping");
    setMapProgress(12);
    setError(null);
    setNavigating(false);
    const here = { lat, lon, bearing: 0 };
    poseRef.current = here;
    setPose(here);
    try {
      const area = await loadArea(lat, lon, 2500, noteArea, () => loadGen.current !== gen);
      if (loadGen.current !== gen) return;
      if (area.place) setPlace(area.place);
      adoptGraph(area.graph);
      const [map, feed] = await Promise.all([fetchOverlay(area.graph), fetchFeed(0, area.graph)]);
      setOverlay(map);
      remember(feed.events, false);
      liveAfter.current = feed.cursor;
      armed.current = true;
      await refreshRoute(lat, lon, "load", area.graph);
      setMapProgress(100);
      setPhase("ready");
    } catch (exc) {
      if (loadGen.current !== gen) return;
      revealCities();
      setError(exc instanceof Error ? exc.message : `${city.label} failed to load`);
    }
  }

  function chooseDestination(id: string) {
    destRef.current = id;
    const here = poseRef.current;
    setNavigating(false);
    return refreshRoute(here.lat, here.lon, "move", graphRef.current, id).catch((exc: unknown) =>
      setError(exc instanceof Error ? exc.message : "Could not change destination"),
    );
  }

  useEffect(() => {
    if (travel !== "live" || !navigator.geolocation) return;
    const id = navigator.geolocation.watchPosition(
      (position) => {
        const lat = position.coords.latitude;
        const lon = position.coords.longitude;
        lastFix.current = { lat, lon };
        const coords = routeRef.current?.geojson.geometry.coordinates ?? null;
        let bearing = poseRef.current.bearing;
        if (coords && coords.length >= 2) {
          const metrics = lineMetrics(coords);
          const along = snapProgress(coords, metrics.cum, { lat, lon });
          bearing = pointAlong(coords, metrics.cum, along).bearing;
          if (navigatingRef.current) {
            progressRef.current = along;
            setProgress(along);
          }
        }
        const next = { lat, lon, bearing };
        poseRef.current = next;
        setPose(next);
      },
      () => undefined,
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 10000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, [travel]);

  useEffect(() => {
    let cancelled = false;
    const geo = navigator.geolocation;
    if (!geo) {
      setPhase("choose");
      return;
    }
    void (async () => {
      let permission: PermissionState | "unknown" = "unknown";
      try {
        const status = await navigator.permissions?.query({ name: "geolocation" });
        permission = status?.state ?? "unknown";
      } catch {
        permission = "unknown";
      }
      if (cancelled) return;
      if (permission === "denied") {
        revealCities();
        return;
      }
      setMappingTitle("Mapping streets around you…");
      setPhase("mapping");
      setMapProgress(8);
      geo.getCurrentPosition(
        (position) => {
          if (!cancelled) void loadAround(position.coords.latitude, position.coords.longitude);
        },
        () => {
          if (cancelled) return;
          revealCities();
        },
        { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 },
      );
    })();
    return () => {
      cancelled = true;
    };
    // Ask once on first paint. Granted permission loads this tile; a denial shows the chooser.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function goToPlace(place: SavedPlace) {
    setNavigating(false);
    setTab("escape");
    void refreshRoute(place.lat, place.lon, "move").catch((exc: unknown) =>
      setError(exc instanceof Error ? exc.message : "Could not route"),
    );
  }

  const topSeq = events.reduce((max, event) => Math.max(max, event.seq || 0), 0);
  useEffect(() => {
    if (!showGraph || !overlay?.loaded) {
      setGraphNet(null);
      return;
    }
    let live = true;
    fetchNetworkGraph(graphId)
      .then((net) => live && setGraphNet(net))
      .catch((exc: unknown) => live && setError(exc instanceof Error ? exc.message : "Road graph unavailable"));
    return () => {
      live = false;
    };
  }, [showGraph, graphId, topSeq, overlay?.loaded, overlay?.closed?.length]);

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
    onRetry: () => {
      const mode = modeRef.current;
      if (mode && mode !== "local") void openCity(mode);
      else if (mode === "local") void loadAround(poseRef.current.lat, poseRef.current.lon);
      else locate();
    },
    onCity: (slug: CitySlug) => void openCity(slug),
    onDestination: chooseDestination,
    chooser,
    reportFocus,
    onGo: goToPlace,
    onClearClosures: () => void clearClosures(),
    place,
    mapping: phase === "mapping" || phase === "boot",
    graphId,
    showGraph,
    onShowGraph: () => setShowGraph((value) => !value),
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
        destPoint={
          route?.destination.lat != null && route.destination.lon != null
            ? { lat: route.destination.lat, lon: route.destination.lon }
            : null
        }
        flood={flood}
        navigating={navigating}
        simulate={travel !== "live"}
        progressRef={progressRef}
        paddingRight={mobile ? 12 : width >= 1024 ? 400 : 340}
        mobile={mobile}
        showControls={phase === "ready" || phase === "choose"}
        onSample={onSample}
        onPlace={(place) => {
          setPromptClosure(null);
          setPromptArea(null);
          setPromptPlace(place);
        }}
        onClosed={(roadId) => {
          const road = (overlay?.closed ?? []).find((item) => item.road_id === roadId) ?? null;
          setPromptPlace(null);
          setPromptArea(null);
          setPromptClosure(road);
        }}
        graphNet={graphNet}
        onArea={() => {
          setPromptPlace(null);
          setPromptClosure(null);
          setPromptArea({ lat: poseRef.current.lat, lon: poseRef.current.lon, name: "Your area" });
        }}
      />
      <MapPrompt
        place={promptPlace}
        closure={promptClosure}
        area={promptArea}
        busy={busy}
        onClose={() => {
          setPromptPlace(null);
          setPromptClosure(null);
          setPromptArea(null);
        }}
        onNavigate={(lat, lon) => {
          const pinId = promptPlace?.pinId;
          setPromptPlace(null);
          setNavigating(false);
          if (pinId) {
            void Promise.resolve(chooseDestination(pinId)).then(() => setNavigating(true));
            return;
          }
          setTravel("demo");
          void refreshRoute(lat, lon, "move").then(() => setNavigating(true)).catch((exc: unknown) =>
            setError(exc instanceof Error ? exc.message : "Could not navigate there"),
          );
        }}
        onReport={(lat, lon, name) => {
          const flood = DISASTERS.find((item) => item.id === "flooded");
          setReport(flood ? flood.template(lat, lon, name) : `Flooded road at ${lat.toFixed(5)}, ${lon.toFixed(5)}. The road is under water and not passable.`);
          setPromptPlace(null);
          setTab("report");
          setReportFocus((value) => value + 1);
        }}
        onSetLocation={(lat, lon) => {
          lastFix.current = { lat, lon };
          const next = { lat, lon, bearing: 0 };
          poseRef.current = next;
          setPose(next);
          setPromptPlace(null);
          setNavigating(false);
          void refreshRoute(lat, lon, "move").catch((exc: unknown) =>
            setError(exc instanceof Error ? exc.message : "Could not use that location"),
          );
        }}
        onReopen={(roadId) => void markClear(roadId)}
        onStillFlooded={() => {
          setPromptClosure(null);
          setNotice({ title: "Still flooded", detail: "The road stays closed." });
        }}
        onAreaOk={(lat, lon) => void markAreaOk(lat, lon)}
      />
      <header className="float-brand">
        <span>Floodline</span>
        <strong>{place}</strong>
      </header>
      {(phase === "mapping" || phase === "boot") && (
        <div className="map-loading" role="status" aria-live="polite" aria-busy="true">
          <h2>{mappingTitle}</h2>
          <p>{stageText}</p>
          <div className="progress-track" aria-hidden="true">
            <span style={{ width: `${Math.min(100, Math.round(mapProgress))}%` }} />
          </div>
        </div>
      )}
      {flood && <div className="vision-chip">Flood vision</div>}
      {(phase === "ready" || phase === "choose") && !navigating && (
        <button type="button" className={`map-graph-btn${showGraph ? " on" : ""}`} onClick={() => setShowGraph((value) => !value)}>
          {showGraph ? "Hide graph" : "Show graph"}
        </button>
      )}
      {showGraph && graphNet?.stats && (
        <div className="graph-map-chip" aria-live="polite">
          <span><i style={{ background: "#64D2FF" }} />{String(graphNet.stats.intersections)} nodes · {String(graphNet.stats.edges)} roads</span>
          <span><i style={{ background: "#30D158" }} />route</span>
          <span><i style={{ background: "#FF453A" }} />closed {String(graphNet.stats.closed_edges)}</span>
          <span><i style={{ background: "#BF5AF2" }} />cut off {String(graphNet.stats.cut_off_nodes)}</span>
        </div>
      )}
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
