interface TaskPaneProps {
  tasks: Array<{
    id: string;
    title: string;
    status: string;
    progress?: string;
    details?: string;
  }>;
}

export default function TaskPane({ tasks }: TaskPaneProps) {
  return (
    <div className="pane">
      <h2>Task Queue</h2>
      <div className="scroll-list">
        {tasks.map((task) => (
          <div key={task.id} className="task-item">
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
