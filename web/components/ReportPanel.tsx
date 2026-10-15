import { DISASTERS, matchingDisasters } from "@/lib/disasters";

type Props = {
  text: string;
  busy: boolean;
  loaded: boolean;
  pose: { lat: number; lon: number };
  placeName?: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onAhead: () => void;
};

export function ReportPanel({ text, busy, loaded, pose, placeName, onChange, onSubmit, onAhead }: Props) {
  const matches = matchingDisasters(text).filter((item) => item.template(pose.lat, pose.lon, placeName) !== text);

  function apply(label: string) {
    const disaster = DISASTERS.find((item) => item.label === label);
    if (!disaster) return;
    onChange(disaster.template(pose.lat, pose.lon, placeName));
  }

  return (
    <div className="stack">
      <p className="kicker">Report</p>
      <h2 className="display tight">Close a road</h2>
      <p className="lede">
        A report on the street ahead turns that segment red and bends the route. Scout, Dispatcher, and Analyst show their work in Live.
      </p>
      <div className="chip-row" aria-label="Disaster types">
        {DISASTERS.map((item) => (
          <button key={item.id} type="button" className="suggest" onClick={() => apply(item.label)} disabled={busy}>
            {item.label}
          </button>
        ))}
      </div>
      <label className="field" htmlFor="report-text">
        What do you see?
        <textarea
          id="report-text"
          value={text}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
          disabled={busy}
          autoComplete="off"
          placeholder="Start typing a disaster, or tap a chip"
        />
      </label>
      {matches.length > 0 && (
        <ul className="suggest-list" aria-label="Suggestions">
          {matches.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => apply(item.label)}>
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn danger" onClick={onAhead} disabled={busy || !loaded}>
        Water on the road ahead
      </button>
      <button type="button" className="btn ghost" onClick={onSubmit} disabled={busy || !loaded || text.trim().length === 0}>
        File this report
      </button>
    </div>
  );
}
