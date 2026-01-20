interface DocsOverlayProps {
  docs: Array<{ title?: string; url?: string }>;
  onClose: () => void;
}

export default function DocsOverlay({ docs, onClose }: DocsOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Docs</h3>
        <button type="button" onClick={onClose}>Close</button>
      </header>
      <div className="scroll-list">
        {docs.map((doc, index) => (
          <div key={`${doc.url}-${index}`} className="task-item">
            <div>{doc.title ?? 'Untitled'}</div>
            {doc.url ? (
              <a href={doc.url} target="_blank" rel="noreferrer">
                {doc.url}
              </a>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
