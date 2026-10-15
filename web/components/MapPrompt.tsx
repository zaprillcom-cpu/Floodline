import type { ClosedRoad } from "@/lib/types";

type Place = { lat: number; lon: number; name: string };

type Props = {
  place: Place | null;
  closure: ClosedRoad | null;
  area: Place | null;
  busy: boolean;
  onClose: () => void;
  onNavigate: (lat: number, lon: number) => void;
  onReport: (lat: number, lon: number, name: string) => void;
  onSetLocation: (lat: number, lon: number) => void;
  onReopen: (roadId: string) => void;
  onStillFlooded: () => void;
  onAreaOk: (lat: number, lon: number) => void;
};

export function MapPrompt({
  place,
  closure,
  area,
  busy,
  onClose,
  onNavigate,
  onReport,
  onSetLocation,
  onReopen,
  onStillFlooded,
  onAreaOk,
}: Props) {
  if (!place && !closure && !area) return null;
  return (
    <div className="map-prompt" role="dialog" aria-label={closure ? closure.name : place?.name || "Your area"}>
      <button type="button" className="text-btn prompt-close" onClick={onClose}>
        Close
      </button>
      {closure && (
        <>
          <p className="kicker">Closed road</p>
          <h2 className="display tight">{closure.name}</h2>
          <p className="lede">{closure.closure_type || "Closure"}</p>
          <p className="lede">{closure.report || "No report text was stored for this closure."}</p>
          <dl className="prompt-facts">
            <div>
              <dt>When</dt>
              <dd>{closure.at || closure.closed_at || "—"}</dd>
            </div>
            <div>
              <dt>Agent</dt>
              <dd>{closure.agent || closure.closed_by || "Scout"}</dd>
            </div>
            <div>
              <dt>Decision</dt>
              <dd>{closure.decision_id || "—"}</dd>
            </div>
          </dl>
          <div className="row">
            <button type="button" className="btn" onClick={() => onReopen(closure.road_id)} disabled={busy}>
              Mark as clear / road is OK
            </button>
            <button type="button" className="btn ghost" onClick={onStillFlooded} disabled={busy}>
              Still flooded
            </button>
          </div>
        </>
      )}
      {place && !closure && (
        <>
          <p className="kicker">This place</p>
          <h2 className="display tight">{place.name}</h2>
          <p className="lede">
            {place.lat.toFixed(5)}, {place.lon.toFixed(5)}
          </p>
          <div className="row">
            <button type="button" className="btn" onClick={() => onNavigate(place.lat, place.lon)}>
              Navigate here
            </button>
            <button type="button" className="btn ghost" onClick={() => onReport(place.lat, place.lon, place.name)}>
              Report flooding here
            </button>
            <button type="button" className="btn ghost" onClick={() => onSetLocation(place.lat, place.lon)}>
              Set as my location
            </button>
          </div>
        </>
      )}
      {area && !closure && !place && (
        <>
          <p className="kicker">You</p>
          <h2 className="display tight">Your area</h2>
          <p className="lede">
            {area.lat.toFixed(5)}, {area.lon.toFixed(5)}
          </p>
          <button type="button" className="btn" onClick={() => onAreaOk(area.lat, area.lon)} disabled={busy}>
            My area is OK
          </button>
        </>
      )}
    </div>
  );
}
