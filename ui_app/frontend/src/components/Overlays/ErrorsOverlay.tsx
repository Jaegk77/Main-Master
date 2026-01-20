interface ErrorsOverlayProps {
  errors: string[];
  onClose: () => void;
}

export default function ErrorsOverlay({ errors, onClose }: ErrorsOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Errors</h3>
        <button type="button" onClick={onClose}>Close</button>
      </header>
      <div className="scroll-list">
        {errors.length === 0 ? <div>No recent errors.</div> : null}
        {errors.map((error, index) => (
          <div key={`${error}-${index}`} className="task-item">
            {error}
          </div>
        ))}
      </div>
    </div>
  );
}
