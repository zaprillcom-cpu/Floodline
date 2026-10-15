import type { FeedEvent } from "@/lib/types";

function tone(agent: string): string {
  const name = agent.toLowerCase();
  if (name.includes("scout")) return "scout";
  if (name.includes("dispatch")) return "dispatcher";
  if (name.includes("analyst")) return "analyst";
  return "analyst";
}

export function AgentFeed({ events }: { events: FeedEvent[] }) {
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
              <span className="when">{event.kind || event.type}{event.status ? ` · ${event.status}` : ""}</span>
            </div>
            <strong className="card-title">{event.title}</strong>
            {event.body && <p className="card-body">{event.body}</p>}
            {event.query && <pre className="query">{event.query}</pre>}
          </li>
        ))}
      </ol>
    </div>
  );
}
