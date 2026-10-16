"use client";

import { useEffect, useRef, type MutableRefObject } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

if (typeof window !== "undefined") {
  maplibregl.setWorkerUrl("/maplibre-gl-worker.js");
}
import { lineMetrics, pointAlong, type LonLat, type Pose } from "@/lib/geo";
import { theme } from "@/lib/theme";
import type { ClosedRoad, Pin } from "@/lib/types";

const STYLE = "https://tiles.openfreemap.org/styles/dark";
const SPEED_MPS = 18;

type Zone = { name: string; polygon: [number, number][] };

type Props = {
  center: { lat: number; lon: number; bearing?: number };
  routeCoords: LonLat[] | null;
  routeKey: string;
  closed: ClosedRoad[];
  zones: Zone[];
  pins: Pin[];
  activePinId: string | null;
  flood: boolean;
  navigating: boolean;
  simulate?: boolean;
  destPoint?: { lat: number; lon: number } | null;
  progressRef: MutableRefObject<number>;
  paddingRight: number;
  mobile: boolean;
  showControls?: boolean;
  onSample: (progress: number, pose: Pose) => void;
  onPlace?: (place: { lat: number; lon: number; name: string; pinId?: string }) => void;
  onClosed?: (roadId: string) => void;
  onArea?: () => void;
  graphNet?: GraphNet | null;
};

type FeatureCollection = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    properties: Record<string, string>;
    geometry:
      | { type: "LineString"; coordinates: [number, number][] }
      | { type: "Polygon"; coordinates: [number, number][][] };
  }>;
};

function emptyCollection(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function addBuildings(map: maplibregl.Map, flood: boolean) {
  const color = flood ? theme.color.buildingFlood : theme.color.building;
  if (map.getLayer("floodline-3d-buildings")) {
    map.setPaintProperty("floodline-3d-buildings", "fill-extrusion-color", color);
    return;
  }
  if (!map.getSource("openmaptiles")) return;
  const before = map.getStyle()?.layers?.find((layer: { type?: string; id: string }) => layer.type === "symbol")?.id;
  map.addLayer(
    {
      id: "floodline-3d-buildings",
      type: "fill-extrusion",
      source: "openmaptiles",
      "source-layer": "building",
      minzoom: 14,
      paint: {
        "fill-extrusion-color": color,
        "fill-extrusion-height": [
          "interpolate",
          ["linear"],
          ["zoom"],
          14,
          0,
          15.2,
          ["coalesce", ["get", "render_height"], 10],
        ],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        "fill-extrusion-opacity": 0.94,
      },
    },
    before,
  );
}

function ensure(map: maplibregl.Map) {
  if (!map.getSource("route")) {
    map.addSource("route", { type: "geojson", data: emptyCollection() });
    map.addLayer({
      id: "route-casing",
      type: "line",
      source: "route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": theme.color.routeCasing, "line-width": 11, "line-opacity": 0.9 },
    });
    map.addLayer({
      id: "route-line",
      type: "line",
      source: "route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": theme.color.route, "line-width": 5.5, "line-opacity": 0.95 },
    });
  }
  if (!map.getSource("closed")) {
    map.addSource("closed", { type: "geojson", data: emptyCollection() });
    map.addLayer({
      id: "closed-glow",
      type: "line",
      source: "closed",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": theme.color.closed,
        "line-width": 5,
        "line-blur": 0.4,
        "line-opacity": 0.9,
      },
    });
    map.addLayer({
      id: "closed-hit",
      type: "line",
      source: "closed",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": theme.color.closed, "line-width": 22, "line-opacity": 0.01 },
    });
  }
  if (!map.getSource("zones")) {
    map.addSource("zones", { type: "geojson", data: emptyCollection() });
    map.addLayer({
      id: "zone-fill",
      type: "fill",
      source: "zones",
      paint: { "fill-color": theme.color.purple, "fill-opacity": 0.22 },
    });
    map.addLayer({
      id: "zone-line",
      type: "line",
      source: "zones",
      paint: { "line-color": theme.color.purple, "line-width": 1.5, "line-opacity": 0.8 },
    });
  }
}

export type GraphNet = {
  type: "FeatureCollection";
  features: Array<{ type: "Feature"; geometry: unknown; properties: Record<string, unknown> }>;
  stats?: Record<string, number | boolean>;
};

const GRAPH_LAYERS = ["graph-edges", "graph-route", "graph-closed", "graph-nodes"];

function paintGraph(map: maplibregl.Map, net: GraphNet | null | undefined) {
  if (!map.getSource("graphnet")) {
    map.addSource("graphnet", { type: "geojson", data: emptyCollection() });
    const before = map.getLayer("route-casing") ? "route-casing" : undefined;
    map.addLayer(
      {
        id: "graph-edges",
        type: "line",
        source: "graphnet",
        filter: ["==", ["get", "kind"], "edge"],
        paint: {
          "line-color": ["case", ["get", "cut_off"], theme.color.purple, "rgba(100, 210, 255, 0.55)"],
          "line-width": ["interpolate", ["linear"], ["zoom"], 13, 0.6, 17, 1.6],
        },
      },
      before,
    );
    map.addLayer(
      {
        id: "graph-route",
        type: "line",
        source: "graphnet",
        filter: ["all", ["==", ["get", "kind"], "edge"], ["==", ["get", "on_route"], true]],
        paint: { "line-color": theme.color.green, "line-width": 3.2 },
      },
      before,
    );
    map.addLayer(
      {
        id: "graph-closed",
        type: "line",
        source: "graphnet",
        filter: ["all", ["==", ["get", "kind"], "edge"], ["==", ["get", "status"], "closed"]],
        paint: { "line-color": theme.color.closed, "line-width": 3.4 },
      },
      before,
    );
    map.addLayer(
      {
        id: "graph-nodes",
        type: "circle",
        source: "graphnet",
        filter: ["==", ["get", "kind"], "node"],
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 13, 1.2, 17, 3.4],
          "circle-color": ["case", ["get", "cut_off"], theme.color.purple, theme.color.cyan],
          "circle-opacity": 0.9,
          "circle-stroke-width": 0,
        },
      },
      before,
    );
  }
  setData(map, "graphnet", (net as FeatureCollection | null) ?? emptyCollection());
  for (const id of GRAPH_LAYERS) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", net ? "visible" : "none");
  }
}

function setData(map: maplibregl.Map, id: string, data: FeatureCollection) {
  const source = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  source?.setData(data);
}

function pinElement(pin: Pin, active: boolean): HTMLButtonElement {
  const el = document.createElement("button");
  el.type = "button";
  el.className = active ? `pin pin-${pin.kind} pin-active` : `pin pin-${pin.kind}`;
  el.setAttribute("aria-label", active ? `Destination, ${pin.name}` : pin.name);
  const glyph = document.createElement("span");
  glyph.className = "pin-glyph";
  const label = document.createElement("span");
  label.className = "pin-label";
  label.textContent = pin.name;
  el.append(glyph, label);
  if (active) {
    const badge = document.createElement("span");
    badge.className = "pin-badge";
    badge.textContent = "Destination";
    el.append(badge);
  }
  return el;
}

export function FloodMap({
  center,
  routeCoords,
  routeKey,
  closed,
  zones,
  pins,
  activePinId,
  flood,
  navigating,
  simulate = true,
  destPoint = null,
  progressRef,
  paddingRight,
  mobile,
  showControls = true,
  onSample,
  onPlace,
  onClosed,
  onArea,
  graphNet = null,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const puckRef = useRef<maplibregl.Marker | null>(null);
  const pinsRef = useRef<maplibregl.Marker[]>([]);
  const fitted = useRef("");
  const controlRef = useRef<maplibregl.NavigationControl | null>(null);
  const propsRef = useRef({
    center,
    routeCoords,
    routeKey,
    closed,
    zones,
    pins,
    activePinId,
    flood,
    navigating,
    simulate,
    destPoint,
    progressRef,
    paddingRight,
    mobile,
    showControls,
    onSample,
    onPlace,
    onClosed,
    onArea,
  });
  propsRef.current = {
    center,
    routeCoords,
    routeKey,
    closed,
    zones,
    pins,
    activePinId,
    flood,
    navigating,
    simulate,
    destPoint,
    progressRef,
    paddingRight,
    mobile,
    showControls,
    onSample,
    onPlace,
    onClosed,
    onArea,
  };
  const paintRef = useRef<(map: maplibregl.Map) => void>(() => {});

  useEffect(() => {
    if (!ref.current) return;
    const map = new maplibregl.Map({
      container: ref.current,
      style: STYLE,
      center: [center.lon, center.lat],
      zoom: 15.6,
      pitch: 58,
      bearing: -16,
      maxPitch: 72,
      attributionControl: { compact: true },
      canvasContextAttributes: { antialias: true },
    });
    const puckEl = document.createElement("div");
    puckEl.className = "puck";
    puckEl.addEventListener("click", (event) => {
      event.stopPropagation();
      event.preventDefault();
      propsRef.current.onArea?.();
    });
    const cone = document.createElement("div");
    cone.className = "puck-cone";
    const dot = document.createElement("div");
    dot.className = "puck-dot";
    puckEl.append(cone, dot);
    const puck = new maplibregl.Marker({ element: puckEl, rotationAlignment: "map", pitchAlignment: "map" })
      .setLngLat([center.lon, center.lat])
      .addTo(map);
    mapRef.current = map;
    puckRef.current = puck;
    (map.getContainer() as HTMLElement & { __floodMap?: maplibregl.Map }).__floodMap = map;
    let ignoreClick = false;
    const openAt = (lngLat: { lat: number; lng: number }, point: { x: number; y: number }, retried = false) => {
      const current = propsRef.current;
      if (!map.isStyleLoaded()) {
        if (!retried) map.once("idle", () => openAt(lngLat, point, true));
        return;
      }
      if (map.getLayer("closed-hit")) {
        const hits = map.queryRenderedFeatures([point.x, point.y], { layers: ["closed-hit"] });
        const roadId = hits.find((hit) => hit.properties?.road_id)?.properties?.road_id;
        if (roadId) {
          current.onClosed?.(String(roadId));
          return;
        }
      }
      let name = "This point";
      for (const feature of map.queryRenderedFeatures([point.x, point.y])) {
        const label = feature.properties?.name || feature.properties?.name_en || feature.properties?.ref;
        if (label && feature.layer?.id !== "closed-glow" && feature.layer?.id !== "closed-hit") {
          name = String(label);
          break;
        }
      }
      current.onPlace?.({ lat: lngLat.lat, lon: lngLat.lng, name });
    };
    map.on("click", (event) => {
      if (ignoreClick) {
        ignoreClick = false;
        return;
      }
      openAt(event.lngLat, event.point);
    });
    map.on("contextmenu", (event) => {
      event.preventDefault();
      ignoreClick = true;
      openAt(event.lngLat, event.point);
    });
    const canvas = map.getCanvas();
    let holdTimer = 0;
    let holdStart = { x: 0, y: 0 };
    const pointFrom = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    const cancelHold = () => window.clearTimeout(holdTimer);
    canvas.addEventListener("pointerdown", (event) => {
      ignoreClick = false;
      holdStart = pointFrom(event);
      cancelHold();
      holdTimer = window.setTimeout(() => {
        ignoreClick = true;
        const point = holdStart;
        openAt(map.unproject([point.x, point.y]), point);
      }, 480);
    });
    canvas.addEventListener("pointerup", cancelHold);
    canvas.addEventListener("pointercancel", cancelHold);
    canvas.addEventListener("pointermove", (event) => {
      const point = pointFrom(event);
      if (Math.hypot(point.x - holdStart.x, point.y - holdStart.y) > 8) cancelHold();
    });
    map.on("load", () => {
      map.resize();
      paintRef.current(map);
    });
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      pinsRef.current.forEach((marker) => marker.remove());
      pinsRef.current = [];
      map.remove();
      mapRef.current = null;
      puckRef.current = null;
    };
    // The map is created once. Later props sync in the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const graphRef = useRef<GraphNet | null>(graphNet);
  graphRef.current = graphNet;

  paintRef.current = function paint(map: maplibregl.Map) {
    const current = propsRef.current;
    addBuildings(map, current.flood);
    ensure(map);
    paintGraph(map, graphRef.current);
    const coords = current.routeCoords;
    setData(
      map,
      "route",
      coords && coords.length >= 2
        ? {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                properties: {},
                geometry: { type: "LineString", coordinates: coords },
              },
            ],
          }
        : emptyCollection(),
    );
    setData(map, "closed", {
      type: "FeatureCollection",
      features: current.closed.map((road) => ({
        type: "Feature",
        properties: { name: road.name, road_id: road.road_id },
        geometry: { type: "LineString", coordinates: road.coordinates },
      })),
    });
    setData(map, "zones", {
      type: "FeatureCollection",
      features: current.flood
        ? current.zones.map((zone) => ({
            type: "Feature",
            properties: { name: zone.name },
            geometry: { type: "Polygon", coordinates: [zone.polygon] },
          }))
        : [],
    });
    map.setPaintProperty("closed-glow", "line-width", current.flood ? 12 : 5);
    map.setPaintProperty("closed-glow", "line-blur", current.flood ? 8 : 0.4);
    map.setPaintProperty("closed-glow", "line-opacity", current.flood ? 0.95 : 0.85);
    map.setPadding({
      top: 70,
      bottom: current.mobile ? 200 : 40,
      left: 24,
      right: current.paddingRight,
    });

    pinsRef.current.forEach((marker) => marker.remove());
    pinsRef.current = current.pins.map((pin) => {
      const el = pinElement(pin, pin.id === current.activePinId);
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        event.preventDefault();
        propsRef.current.onPlace?.({ lat: pin.lat, lon: pin.lon, name: pin.name, pinId: pin.id });
      });
      return new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([pin.lon, pin.lat]).addTo(map);
    });

    if (coords && coords.length >= 2 && fitted.current !== current.routeKey && !current.navigating) {
      fitted.current = current.routeKey;
      const bounds = coords.reduce(
        (box, coord) => box.extend(coord),
        new maplibregl.LngLatBounds(coords[0], coords[0]),
      );
      const active = current.pins.find((pin) => pin.id === current.activePinId);
      if (active) bounds.extend([active.lon, active.lat]);
      if (current.destPoint) bounds.extend([current.destPoint.lon, current.destPoint.lat]);
      map.fitBounds(bounds, {
        padding: {
          top: 90,
          bottom: current.mobile ? 230 : 70,
          left: 48,
          right: current.paddingRight + 28,
        },
        pitch: 55,
        bearing: map.getBearing(),
        duration: 1100,
        maxZoom: 16.4,
      });
    }
  }

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    paintRef.current(map);
  }, [routeKey, closed, zones, pins, activePinId, flood, paddingRight, mobile, navigating, destPoint]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (map.isStyleLoaded()) paintGraph(map, graphNet);
    else map.once("idle", () => paintGraph(map, graphRef.current));
  }, [graphNet]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (controlRef.current) {
      map.removeControl(controlRef.current);
      controlRef.current = null;
    }
    if (!showControls) return;
    const control = new maplibregl.NavigationControl({ visualizePitch: true, showCompass: true });
    map.addControl(control, mobile ? "top-right" : "top-left");
    controlRef.current = control;
  }, [showControls, mobile]);

  useEffect(() => {
    if (!navigating || !simulate) return;
    let frame = 0;
    let last = performance.now();
    let lastEmit = 0;
    const loop = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const current = propsRef.current;
      const coords = current.routeCoords;
      const map = mapRef.current;
      const puck = puckRef.current;
      if (coords && coords.length >= 2 && map && puck) {
        const metrics = lineMetrics(coords);
        const next = Math.min(metrics.total, current.progressRef.current + SPEED_MPS * dt);
        current.progressRef.current = next;
        const pose = pointAlong(coords, metrics.cum, next);
        puck.setLngLat([pose.lon, pose.lat]);
        puck.setRotation(pose.bearing);
        if (now - lastEmit > 280) {
          lastEmit = now;
          current.onSample(next, pose);
          map.easeTo({
            center: [pose.lon, pose.lat],
            bearing: pose.bearing,
            pitch: 64,
            zoom: Math.max(map.getZoom(), 16.3),
            duration: 320,
            essential: true,
          });
        }
        if (next >= metrics.total - 1) current.onSample(next, pose);
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [navigating, simulate]);

  useEffect(() => {
    if (!navigating || simulate) return;
    const puck = puckRef.current;
    const map = mapRef.current;
    if (!puck || !map) return;
    puck.setLngLat([center.lon, center.lat]);
    if (typeof center.bearing === "number") puck.setRotation(center.bearing);
    map.easeTo({
      center: [center.lon, center.lat],
      bearing: typeof center.bearing === "number" ? center.bearing : map.getBearing(),
      pitch: 64,
      zoom: Math.max(map.getZoom(), 16.3),
      duration: 700,
      essential: true,
    });
  }, [navigating, simulate, center.lat, center.lon, center.bearing]);

  useEffect(() => {
    if (navigating) return;
    const puck = puckRef.current;
    const map = mapRef.current;
    if (!puck) return;
    puck.setLngLat([center.lon, center.lat]);
    if (!map) return;
    const here = map.getCenter();
    if (Math.abs(here.lat - center.lat) > 0.01 || Math.abs(here.lng - center.lon) > 0.01) {
      map.easeTo({
        center: [center.lon, center.lat],
        zoom: 15.2,
        pitch: 58,
        duration: 900,
        essential: true,
      });
    }
  }, [center.lat, center.lon, navigating]);

  return <div ref={ref} className="map-canvas" />;
}
