interface CandidatesOverlayProps {
  onClose: () => void;
}

export default function CandidatesOverlay({ onClose }: CandidatesOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Candidates</h3>
        <button type="button" onClick={onClose}>Close</button>
      </header>
      <div className="scroll-list">
        <div className="task-item">
          Candidate list placeholder. Hook into agent candidate proposals here.
        </div>
      </div>
    </div>
  );
}
