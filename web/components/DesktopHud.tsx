"use client";

import { useState } from "react";
import { AgentFeed } from "@/components/AgentFeed";
import { EscapePanel } from "@/components/EscapePanel";
import { MePanel } from "@/components/MePanel";
import { ReportPanel } from "@/components/ReportPanel";
import type { FeedEvent, Overlay, RoutePlan, TabId } from "@/lib/types";
import type { SavedPlace } from "@/lib/storage";

const TABS: { id: TabId; label: string }[] = [
  { id: "escape", label: "Escape" },
  { id: "report", label: "Report" },
  { id: "live", label: "Live" },
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
  onDemo: () => void;
  onGo: (place: SavedPlace) => void;
  place: string;
  mapping: boolean;
};

export function DesktopHud(props: Props) {
  const [tab, setTab] = useState<TabId>("escape");
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
            onDemo={props.onDemo}
            place={props.place}
            mapping={props.mapping}
          />
        )}
        {tab === "report" && (
          <ReportPanel
            text={props.report}
            busy={props.busy}
            loaded={Boolean(props.overlay?.loaded)}
            onChange={props.onReport}
            onSubmit={props.onSubmit}
            onAhead={props.onAhead}
          />
        )}
        {tab === "live" && <AgentFeed events={props.events} />}
        {tab === "me" && <MePanel pose={props.pose} onGo={props.onGo} />}
      </div>
    </aside>
  );
}
