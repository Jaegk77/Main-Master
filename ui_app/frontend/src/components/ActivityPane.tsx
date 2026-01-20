interface ActivityPaneProps {
  events: Array<Record<string, string | number>>;
}

export default function ActivityPane({ events }: ActivityPaneProps) {
  return (
    <div className="pane">
      <h2>Activity / Trace</h2>
      <div className="scroll-list">
        {events.map((event, index) => (
          <div key={`${event.ts}-${index}`} className="task-item">
            <div className="task-status">{event.phase}</div>
            <div>{event.tool}</div>
            <div>{event.result}</div>
            <div>{event.duration_ms}ms</div>
          </div>
        ))}
      </div>
    </div>
  );
}
