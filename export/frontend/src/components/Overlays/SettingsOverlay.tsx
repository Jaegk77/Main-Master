interface SettingsOverlayProps {
  budgets: Record<string, number>;
  model: string;
  onClose: () => void;
}

export default function SettingsOverlay({ budgets, model, onClose }: SettingsOverlayProps) {
  return (
    <div className="overlay">
      <header>
        <h3>Settings</h3>
        <button type="button" onClick={onClose}>Close</button>
      </header>
      <div className="scroll-list">
        <div className="task-item">
          <strong>Model</strong>
          <div>{model}</div>
        </div>
        <div className="task-item">
          <strong>Budgets</strong>
          <div>Max tool calls per tick: {budgets.max_tool_calls_per_tick}</div>
          <div>Max web fetches per tick: {budgets.max_web_fetches_per_tick}</div>
          <div>Max LLM calls per tick: {budgets.max_llm_calls_per_tick}</div>
        </div>
      </div>
    </div>
  );
}
