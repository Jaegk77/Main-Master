interface LogsOverlayProps {
  lines: string[];
  onClose: () => void;
  onRefresh: () => void;
}

export default function LogsOverlay({ lines, onClose, onRefresh }: LogsOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Logs Tail</h3>
        <div>
          <button type="button" onClick={onRefresh}>Refresh</button>{' '}
          <button type="button" onClick={onClose}>Close</button>
        </div>
      </header>
      <div className="scroll-list">
        <pre>{lines.join('\n')}</pre>
      </div>
    </div>
  );
}
