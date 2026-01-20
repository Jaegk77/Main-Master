interface FilesOverlayProps {
  files: string[];
  content: string;
  onClose: () => void;
  onOpen: (path: string) => void;
}

export default function FilesOverlay({ files, content, onClose, onOpen }: FilesOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Workspace Files</h3>
        <button type="button" onClick={onClose}>Close</button>
      </header>
      <div className="scroll-list">
        {files.map((file) => (
          <div key={file} className="task-item">
            <button type="button" onClick={() => onOpen(file)}>
              {file}
            </button>
          </div>
        ))}
      </div>
      {content ? (
        <div className="scroll-list">
          <pre>{content}</pre>
        </div>
      ) : null}
    </div>
  );
}
