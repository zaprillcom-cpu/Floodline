"use client";

import { useState } from "react";
import { AgentFeed } from "@/components/AgentFeed";
import { BottomSheet } from "@/components/BottomSheet";
import { EscapePanel } from "@/components/EscapePanel";
import { IconEscape, IconLive, IconMe, IconReport } from "@/components/Icons";
import { MePanel } from "@/components/MePanel";
import { ReportPanel } from "@/components/ReportPanel";
import type { CitySlug } from "@/lib/cities";
import type { FeedEvent, Overlay, RoutePlan, TabId } from "@/lib/types";
import type { SavedPlace } from "@/lib/storage";

const TABS: { id: TabId; label: string; Icon: typeof IconEscape }[] = [
  { id: "escape", label: "Escape", Icon: IconEscape },
  { id: "report", label: "Report", Icon: IconReport },
  { id: "live", label: "Live", Icon: IconLive },
  { id: "me", label: "Me", Icon: IconMe },
];

type Notice = { title: string; detail: string } | null;

type Props = {
  tab: TabId;
  onTab: (tab: TabId) => void;
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
  chooser?: number;
  onGo: (place: SavedPlace) => void;
  onClearClosures: () => void;
  place: string;
  mapping: boolean;
};

export function MobileShell(props: Props) {
  const [snap, setSnap] = useState(1);

  function select(tab: TabId) {
    props.onTab(tab);
    setSnap((current) => (current === 0 ? 1 : current));
  }

  return (
    <>
      <BottomSheet snap={snap} onSnap={setSnap}>
        {props.tab === "escape" && (
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
        {props.tab === "report" && (
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
        {props.tab === "live" && <AgentFeed events={props.events} />}
        {props.tab === "me" && (
          <MePanel
            pose={props.pose}
            onGo={props.onGo}
            onClearClosures={props.onClearClosures}
            clearBusy={props.busy}
          />
        )}
      </BottomSheet>
      <nav className="tabbar" role="tablist" aria-label="Floodline">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={props.tab === id}
            onClick={() => select(id)}
          >
            <Icon active={props.tab === id} />
            {label}
          </button>
        ))}
      </nav>
    </>
  );
}
