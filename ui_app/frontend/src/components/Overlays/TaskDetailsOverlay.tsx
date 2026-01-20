interface TaskDetailsOverlayProps {
  task: {
    id: string;
    title: string;
    status: string;
    progress?: string;
    details?: string;
  };
  onClose: () => void;
}

export default function TaskDetailsOverlay({ task, onClose }: TaskDetailsOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Task Details</h3>
        <button type="button" onClick={onClose}>Close</button>
      </header>
      <div className="scroll-list">
        <div className="task-item">
          <strong>ID</strong>
          <div>{task.id}</div>
        </div>
        <div className="task-item">
          <strong>Title</strong>
          <div>{task.title}</div>
        </div>
        <div className="task-item">
          <strong>Status</strong>
          <div>{task.status}</div>
        </div>
        <div className="task-item">
          <strong>Progress</strong>
          <div>{task.progress}</div>
        </div>
        <div className="task-item">
          <strong>Details</strong>
          <div>{task.details}</div>
        </div>
      </div>
    </div>
  );
}
