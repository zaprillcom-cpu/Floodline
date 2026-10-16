"use client";

import { useEffect, useState } from "react";
import type { CitySlug } from "@/lib/cities";
import { AgentFeed } from "@/components/AgentFeed";
import { GraphPanel } from "@/components/GraphPanel";
import { EscapePanel } from "@/components/EscapePanel";
import { MePanel } from "@/components/MePanel";
import { ReportPanel } from "@/components/ReportPanel";
import type { FeedEvent, Overlay, RoutePlan, TabId } from "@/lib/types";
import type { SavedPlace } from "@/lib/storage";

const TABS: { id: TabId; label: string }[] = [
  { id: "escape", label: "Escape" },
  { id: "report", label: "Report" },
  { id: "live", label: "Live" },
  { id: "graph", label: "Graph" },
  { id: "me", label: "Me" },
];

type Notice = { title: string; detail: string } | null;

type Props = {
  route: RoutePlan | null;
  progress: number;
  total: number;
  navigating: boolean;
  busy: boolean;
  error: string | null;
  overlay: Overlay | null;
  flood: boolean;
  notice: Notice;
  events: FeedEvent[];
  report: string;
  pose: { lat: number; lon: number };
  onReport: (value: string) => void;
  onSubmit: () => void;
  onAhead: () => void;
  onStart: () => void;
  onStop: () => void;
  onLocate: () => void;
  onFlood: () => void;
  onRetry: () => void;
  onCity: (slug: CitySlug) => void;
  onDestination: (id: string) => void;
  onGo: (place: SavedPlace) => void;
  onClearClosures: () => void;
  place: string;
  mapping: boolean;
  chooser: number;
  reportFocus: number;
  graphId: string | null;
  showGraph: boolean;
  onShowGraph: () => void;
};

export function DesktopHud(props: Props) {
  const [tab, setTab] = useState<TabId>("escape");
  useEffect(() => {
    if (props.chooser > 0) setTab("escape");
  }, [props.chooser]);
  useEffect(() => {
    if (props.reportFocus > 0) setTab("report");
  }, [props.reportFocus]);
  return (
    <aside className="hud" aria-label="Floodline">
      <header className="brand">
        <span>Floodline</span>
        <strong>{props.place}</strong>
      </header>
      <div className="seg" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="hud-scroll">
        {tab === "escape" && (
          <EscapePanel
            route={props.route}
            progress={props.progress}
            total={props.total}
            navigating={props.navigating}
            busy={props.busy}
            error={props.error}
            overlay={props.overlay}
            flood={props.flood}
            notice={props.notice}
            onStart={props.onStart}
            onStop={props.onStop}
            onLocate={props.onLocate}
            onFlood={props.onFlood}
            onRetry={props.onRetry}
            onCity={props.onCity}
            onDestination={props.onDestination}
            place={props.place}
            mapping={props.mapping}
          />
        )}
        {tab === "report" && (
          <ReportPanel
            text={props.report}
            busy={props.busy}
            loaded={Boolean(props.overlay?.loaded)}
            pose={props.pose}
            placeName={props.place}
            onChange={props.onReport}
            onSubmit={props.onSubmit}
            onAhead={props.onAhead}
          />
        )}
        {tab === "live" && <AgentFeed events={props.events} />}
        {tab === "graph" && (
          <GraphPanel graphId={props.graphId} events={props.events} showMap={props.showGraph} onShowMap={props.onShowGraph} height={380} />
        )}
        {tab === "me" && (
          <MePanel
            pose={props.pose}
            onGo={props.onGo}
            onClearClosures={props.onClearClosures}
            clearBusy={props.busy}
          />
        )}
      </div>
    </aside>
  );
}
