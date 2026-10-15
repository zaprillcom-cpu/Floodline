type Props = {
  text: string;
  busy: boolean;
  loaded: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onAhead: () => void;
};

export function ReportPanel({ text, busy, loaded, onChange, onSubmit, onAhead }: Props) {
  return (
    <div className="stack">
      <p className="kicker">Report</p>
      <h2 className="display tight">Close a road</h2>
      <p className="lede">
        A report on the street ahead turns that segment red and bends the route. Scout, Dispatcher, and Analyst show their work in Live.
      </p>
      <label className="field" htmlFor="report-text">
        What do you see?
        <textarea
          id="report-text"
          value={text}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
          disabled={busy}
        />
      </label>
      <button type="button" className="btn danger" onClick={onAhead} disabled={busy || !loaded}>
        Water on the road ahead
      </button>
      <button type="button" className="btn ghost" onClick={onSubmit} disabled={busy || !loaded || text.trim().length === 0}>
        File this report
      </button>
    </div>
  );
}
