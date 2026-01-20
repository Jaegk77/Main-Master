import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "workspace" / "state"
TASKS_PATH = STATE_DIR / "tasks.json"
EVENTS_PATH = STATE_DIR / "events.jsonl"


@dataclass
class QueueState:
    tasks: List[Dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, data: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(data)
    tmp_path.replace(path)


def load_queue() -> QueueState:
    _ensure_state_dir()
    if TASKS_PATH.exists():
        data = json.loads(TASKS_PATH.read_text())
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
    else:
        tasks = []
    return QueueState(tasks=tasks)


def save_queue(state: QueueState) -> None:
    _ensure_state_dir()
    payload = {"tasks": state.tasks}
    _atomic_write(TASKS_PATH, json.dumps(payload, indent=2, ensure_ascii=False))


def append_event(event_type: str, task_id: str, payload: Dict[str, Any]) -> None:
    _ensure_state_dir()
    record = {
        "ts": _now_iso(),
        "type": event_type,
        "task_id": task_id,
        "payload": payload,
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def add_task(
    state: QueueState,
    title: str,
    goal: str,
    steps: List[Dict[str, Any]],
    priority: int = 50,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _now_iso()
    task_id = f"task-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
    task = {
        "id": task_id,
        "title": title,
        "goal": goal,
        "status": "pending",
        "priority": int(priority),
        "created_ts": now,
        "updated_ts": now,
        "next_run_ts": now,
        "cursor": 0,
        "steps": steps,
        "context": context or {},
        "last_error": "",
    }
    state.tasks.append(task)
    append_event("task_added", task_id, {"title": title, "goal": goal})
    return task


def _is_runnable(task: Dict[str, Any], now_ts: str) -> bool:
    status = task.get("status")
    next_run_ts = task.get("next_run_ts")
    if status not in {"pending", "in_progress"}:
        return False
    if not next_run_ts:
        return True
    return next_run_ts <= now_ts


def get_next_runnable_task(state: QueueState, now_ts: str) -> Optional[Dict[str, Any]]:
    runnable = [task for task in state.tasks if _is_runnable(task, now_ts)]
    if not runnable:
        return None
    runnable.sort(key=lambda t: (-int(t.get("priority", 0)), t.get("created_ts", "")))
    return runnable[0]


def _update_task(state: QueueState, task_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
    for task in state.tasks:
        if task.get("id") == task_id:
            task.update(updates)
            task["updated_ts"] = _now_iso()
            return task
    return None


def mark_in_progress(state: QueueState, task_id: str) -> None:
    _update_task(state, task_id, status="in_progress")
    append_event("task_in_progress", task_id, {})


def mark_blocked(state: QueueState, task_id: str, reason: str, retry_after_seconds: int) -> None:
    next_run = datetime.now(timezone.utc).timestamp() + retry_after_seconds
    next_run_ts = datetime.fromtimestamp(next_run, timezone.utc).isoformat()
    _update_task(
        state,
        task_id,
        status="blocked",
        last_error=reason,
        next_run_ts=next_run_ts,
    )
    append_event("task_blocked", task_id, {"reason": reason, "retry_after": retry_after_seconds})


def mark_done(state: QueueState, task_id: str) -> None:
    _update_task(state, task_id, status="done", last_error="")
    append_event("task_done", task_id, {})


def mark_failed(state: QueueState, task_id: str, reason: str) -> None:
    _update_task(state, task_id, status="failed", last_error=reason)
    append_event("task_failed", task_id, {"reason": reason})


def advance_cursor(state: QueueState, task_id: str) -> None:
    task = _update_task(state, task_id)
    if not task:
        return
    task["cursor"] = int(task.get("cursor", 0)) + 1
    append_event("task_advance", task_id, {"cursor": task["cursor"]})
