import type { FeedEvent } from "@/lib/types";

function formatWhen(at?: string): string {
  if (!at) return "";
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return at;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

const GENERIC_ROAD = new Set([
  "",
  "unnamed road",
  "road",
  "residential",
  "service",
  "unclassified",
  "living street",
  "living_street",
  "tertiary",
  "tertiary link",
  "tertiary_link",
  "secondary",
  "secondary link",
  "secondary_link",
  "primary",
  "primary link",
  "primary_link",
  "trunk",
  "trunk link",
  "trunk_link",
  "motorway",
  "motorway link",
  "motorway_link",
  "track",
  "busway",
]);

export function genericRoadName(name: string): boolean {
  const spaced = name.trim().toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return GENERIC_ROAD.has(spaced) || GENERIC_ROAD.has(spaced.replace(/ /g, "_"));
}

export function dedupeRouteTitle(title: string): string {
  const text = (title || "").trim();
  const arrow = text.includes("→") ? "→" : text.includes("->") ? "->" : "";
  if (!arrow) return title || "";
  let body = text;
  let suffix = "";
  if (body.endsWith(")") && body.includes("(")) {
    const cut = body.lastIndexOf("(");
    const tail = body.slice(cut + 1);
    if (!tail.includes("→") && !tail.includes("->")) {
      body = body.slice(0, cut).trim();
      suffix = ` (${tail}`;
    }
  }
  const collapsed: string[] = [];
  let sawGeneric = false;
  for (const part of body.split(arrow)) {
    const name = part.trim();
    if (!name) continue;
    if (genericRoadName(name)) {
      sawGeneric = true;
      continue;
    }
    if (collapsed.length && collapsed[collapsed.length - 1].toLowerCase() === name.toLowerCase()) continue;
    collapsed.push(name);
  }
  if (collapsed.length === 0) return sawGeneric ? `unnamed road${suffix}` : title || "";
  return `${collapsed.join(` ${arrow} `)}${suffix}`;
}

export function cardTitle(event: FeedEvent): string {
  if (event.kind === "duplicate") return "Duplicate of earlier report — no new work";
  return dedupeRouteTitle(event.title);
}

function tone(agent: string): string {
  const name = agent.toLowerCase();
  if (name.includes("scout")) return "scout";
  if (name.includes("dispatch")) return "dispatcher";
  if (name.includes("analyst")) return "analyst";
  return "analyst";
}

export function AgentFeed({ events, onOpenReport }: { events: FeedEvent[]; onOpenReport?: (reportId: string) => void }) {
  const visible = events.filter((event) => event.type !== "heartbeat" && event.type !== "route");
  return (
    <div className="stack">
      <p className="kicker">Live</p>
      <h2 className="display tight">Agents</h2>
      <p className="lede">Scout, Dispatcher, and Analyst, from the Task and Decision nodes. The query under each card is the one FalkorDB ran.</p>
      {visible.length === 0 && <p className="empty">Waiting for the first report. The feed reconnects if the stream drops.</p>}
      <ol className="feed">
        {visible.map((event) => (
          <li key={`${event.type}-${event.id ?? event.title}-${event.seq}`} className="card">
            <div className="card-top">
              <span className={`agent ${tone(event.agent)}`}>{event.agent}</span>
              <span className="when">
                {formatWhen(event.at)}
                {event.planner ? ` · planner ${event.planner}` : ""}
                {event.kind || event.type ? ` · ${event.kind || event.type}` : ""}
                {event.status ? ` ${event.status}` : ""}
              </span>
            </div>
            <strong className="card-title">{cardTitle(event)}</strong>
            {event.kind === "duplicate" && event.duplicate_of && (
              <p className="card-body">
                <a
                  className="report-link"
                  href={`#report-${event.duplicate_of}`}
                  onClick={(click) => {
                    click.preventDefault();
                    onOpenReport?.(event.duplicate_of || "");
                  }}
                >
                  {event.duplicate_of}
                </a>
              </p>
            )}
            {event.reasoning && <p className="card-body">Reasoning. {event.reasoning}</p>}
            {event.body && <p className="card-body">{event.body}</p>}
            {event.query && <pre className="query">{event.query}</pre>}
          </li>
        ))}
      </ol>
    </div>
  );
}
