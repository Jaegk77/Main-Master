interface NavigatorPaneProps {
  active: string | null;
  onToggle: (name: string) => void;
  autopilot: boolean;
  mode: string;
  onTick: () => void;
  onAutopilot: (on: boolean) => void;
}

const NAV_ITEMS = [
  'Logs',
  'Docs',
  'Files',
  'Settings',
  'Candidates',
  'Errors'
];

export default function NavigatorPane({
  active,
  onToggle,
  autopilot,
  mode,
  onTick,
  onAutopilot
}: NavigatorPaneProps) {
  return (
    <div className="pane navigator">
      <h2>Navigator</h2>
      <div className="mode-indicator">Mode: {mode}</div>
      <div className="controls">
        <button type="button" onClick={onTick}>
          Tick
        </button>
        <button type="button" onClick={() => onAutopilot(!autopilot)}>
          Autopilot: {autopilot ? 'On' : 'Off'}
        </button>
      </div>
      <div className="controls">
        <button type="button" onClick={() => onAutopilot(false)}>
          Stop
        </button>
      </div>
      <div style={{ marginTop: '12px' }}>
        {NAV_ITEMS.map((item) => (
          <button
            key={item}
            type="button"
            className={active === item ? 'active' : ''}
            onClick={() => onToggle(item)}
          >
            {item}
          </button>
        ))}
      </div>
    </div>
  );
}
