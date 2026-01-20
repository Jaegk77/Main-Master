interface TaskPaneProps {
  tasks: Array<{
    id: string;
    title: string;
    status: string;
    progress?: string;
    details?: string;
  }>;
  onSelect: (task: { id: string; title: string; status: string; progress?: string; details?: string }) => void;
}

export default function TaskPane({ tasks, onSelect }: TaskPaneProps) {
  return (
    <div className="pane">
      <h2>Task Queue</h2>
      <div className="scroll-list">
        {tasks.map((task) => (
          <div key={task.id} className="task-item" role="button" tabIndex={0} onClick={() => onSelect(task)}>
            <div className="task-status">{task.status}</div>
            <strong>{task.title}</strong>
            <div>{task.progress}</div>
            <div>{task.details}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
