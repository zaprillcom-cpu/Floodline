import { BUNDLED_CITIES, type CitySlug } from "@/lib/cities";
import { formatDistance } from "@/lib/geo";
import { formatEta, type Overlay, type RoutePlan } from "@/lib/types";

function placeHead(role: string | undefined, kind: string | undefined): string {
  if (role === "hospital" || kind === "hospital") return "Hospital";
  return "Shelter";
}

function aboutMinutes(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `about ${minutes} min`;
}

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
  onStart: () => void;
  onStop: () => void;
  onLocate: () => void;
  onFlood: () => void;
  onRetry: () => void;
  onCity: (slug: CitySlug) => void;
  onDestination: (id: string) => void;
  place?: string;
  mapping?: boolean;
};

export function EscapePanel({
  route,
  progress,
  total,
  navigating,
  busy,
  error,
  overlay,
  flood,
  notice,
  onStart,
  onStop,
  onLocate,
  onFlood,
  onRetry,
  onCity,
  onDestination,
  place = "Nearby",
  mapping = false,
}: Props) {
  const dest = route?.destination;
  const remainRatio = total > 0 ? Math.max(0, 1 - progress / total) : 1;
  const eta = route ? Math.round(route.eta_s * remainRatio) : null;
  const tripM = route?.distance_m ?? total;
  const choices = (route?.alternatives ?? []).slice(0, 5);
  const capacity = dest && dest.capacity > 0 ? Math.min(1, dest.occupancy / dest.capacity) : null;
  const loaded = overlay?.loaded;

  return (
    <div className="stack">
      <p className="kicker">Escape</p>
      <h2 className="display">{eta == null ? place : formatEta(eta)}</h2>
      <p className="lede">
        {dest
          ? `to ${dest.name}`
          : loaded
            ? `Nearest open shelter on the ${place} street graph.`
            : "The street graph is not loaded yet."}
      </p>
      {dest && (
        <div className="dest-card" aria-label="Destination">
          <p className="kicker">Destination</p>
          <strong>
            {placeHead(dest.kind, dest.subtype)}: {dest.name}
          </strong>
          <p>
            {formatDistance(tripM)}, {aboutMinutes(route?.eta_s ?? 0)}
          </p>
        </div>
      )}
      {choices.length > 0 && (
        <div className="dest-list" aria-label="Change destination">
          <p className="kicker">Change destination</p>
          {choices.map((choice) => (
            <button
              key={choice.id}
              type="button"
              className={choice.id === dest?.id ? "dest-option current" : "dest-option"}
              onClick={() => onDestination(choice.id)}
              disabled={busy || choice.id === dest?.id}
            >
              <span>
                {placeHead(choice.role, choice.kind)}: {choice.name}
              </span>
              <strong>{formatDistance(choice.distance_m)}</strong>
            </button>
          ))}
        </div>
      )}
      {dest && (
        <div className="stat-row">
          <div>
            <span>Router</span>
            <strong>{(route?.algo || overlay?.router || "—").toUpperCase()}</strong>
          </div>
          <div>
            <span>{dest.metric === "beds" ? "Beds taken" : "Inside"}</span>
            <strong>
              {dest.capacity > 0 ? `${dest.occupancy} / ${dest.capacity}` : "—"}
            </strong>
          </div>
          <div>
            <span>Left</span>
            <strong>{formatDistance(Math.max(0, total - progress))}</strong>
          </div>
        </div>
      )}
      {capacity != null && (
        <div className="meter" aria-hidden="true">
          <span style={{ width: `${Math.round(capacity * 100)}%` }} />
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          <strong>{notice.title}</strong>
          <p>{notice.detail}</p>
        </div>
      )}
      {error && (
        <div className="notice warn" role="alert">
          {error}
        </div>
      )}
      <div className="row">
        {navigating ? (
          <button type="button" className="btn" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="button" className="btn" onClick={onStart} disabled={!route || busy}>
            Start navigation
          </button>
        )}
        <button type="button" className="btn ghost" onClick={onLocate}>
          Use my location
        </button>
      </div>
      <div className="switch-row">
        <div>
          <strong>Flood vision</strong>
          <p>Tint the city, glow closed roads, shade cut-off zones.</p>
        </div>
        <button type="button" className={flood ? "switch on" : "switch"} role="switch" aria-checked={flood} onClick={onFlood}>
          <span />
        </button>
      </div>
      <div className="stat-row slim">
        <div>
          <span>Intersections</span>
          <strong>{overlay?.counts?.intersections ?? "—"}</strong>
        </div>
        <div>
          <span>ROAD edges</span>
          <strong>{overlay?.counts?.road_edges ?? "—"}</strong>
        </div>
        <div>
          <span>Open</span>
          <strong>{overlay?.counts?.passable ?? "—"}</strong>
        </div>
      </div>
      {!mapping && (
        <div className="city-picker" aria-label="Bundled cities">
          <p className="kicker">Bundled cities</p>
          <div className="city-row">
            {BUNDLED_CITIES.map((city) => (
              <button
                key={city.slug}
                type="button"
                className="btn ghost"
                onClick={() => onCity(city.slug)}
                disabled={busy}
              >
                {city.label}
              </button>
            ))}
          </div>
        </div>
      )}
      {error && !mapping && (
        <button type="button" className="btn ghost" onClick={onRetry} disabled={busy}>
          Retry
        </button>
      )}
    </div>
  );
}
