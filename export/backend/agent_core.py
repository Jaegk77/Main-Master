from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass
class TraceEvent:
    ts: str
    phase: str
    req_id: str
    duration_ms: int
    tool: Optional[str] = None
    args: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TaskItem:
    id: str
    title: str
    status: str
    progress: str = ""
    details: str = ""


@dataclass
class RuntimeState:
    tasks: List[TaskItem] = field(default_factory=list)
    events: List[TraceEvent] = field(default_factory=list)
    docs: List[Dict[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    mode: str = "idle"
    autopilot: bool = False
    budgets: Dict[str, int] = field(
        default_factory=lambda: {
            "max_tool_calls_per_tick": 5,
            "max_web_fetches_per_tick": 2,
            "max_llm_calls_per_tick": 1,
        }
    )


class AgentRuntime:
    _instance: Optional["AgentRuntime"] = None

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.state = RuntimeState()
        self.state_dir = self.workspace_root / "state"
        self.logs_dir = self.workspace_root / "logs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.logger = _setup_logger(self.logs_dir / "yesman.log")
        self.tasks_path = self.state_dir / "tasks.json"
        self.events_path = self.state_dir / "events.jsonl"
        self._load_state()

    @classmethod
    def get(cls, workspace_root: Path) -> "AgentRuntime":
        if cls._instance is None:
            cls._instance = AgentRuntime(workspace_root)
        return cls._instance

    def _load_state(self) -> None:
        if self.tasks_path.exists():
            try:
                raw = self.tasks_path.read_text()
                data = json.loads(raw)
                tasks_payload = self._extract_tasks_payload(data)
                if tasks_payload is None:
                    self._mark_tasks_corrupt("Unexpected JSON shape.")
                else:
                    self.state.tasks = tasks_payload
            except json.JSONDecodeError:
                self._mark_tasks_corrupt("Invalid JSON in tasks.json.")
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Failed to load tasks.json: %s", exc)
                self.state.tasks = []
        if self.events_path.exists():
            try:
                lines = self.events_path.read_text().splitlines()[-200:]
                for line in lines:
                    if line.strip():
                        payload = json.loads(line)
                        self.state.events.append(TraceEvent(**payload))
            except json.JSONDecodeError:
                self.state.errors.append("Failed to load persisted events.")

    def _persist_tasks(self) -> None:
        self.tasks_path.write_text(
            json.dumps([task.__dict__ for task in self.state.tasks], indent=2)
        )

    def _extract_tasks_payload(self, data: Any) -> Optional[List[TaskItem]]:
        tasks_data: Any
        if isinstance(data, dict):
            tasks_data = data.get("tasks", [])
            if not isinstance(tasks_data, list):
                return None
        elif isinstance(data, list):
            tasks_data = data
        elif isinstance(data, str):
            return None
        else:
            return None

        tasks: List[TaskItem] = []
        for item in tasks_data:
            if isinstance(item, dict):
                task = _parse_task_item(item)
                if task is not None:
                    tasks.append(task)
                else:
                    self.logger.warning(
                        "Skipping invalid task item in tasks.json: %s", item
                    )
            elif isinstance(item, str):
                self.logger.warning(
                    "Skipping string task item in tasks.json: %s", item
                )
            else:
                self.logger.warning(
                    "Skipping unsupported task item in tasks.json: %s", item
                )
        if len(tasks) != len(tasks_data):
            self.logger.warning("Recovered tasks.json with %d valid tasks.", len(tasks))
        return tasks

    def _mark_tasks_corrupt(self, reason: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        corrupt_path = self.tasks_path.with_name(
            f"tasks.json.corrupt.{timestamp}"
        )
        try:
            self.tasks_path.rename(corrupt_path)
            self.logger.warning(
                "tasks.json marked corrupt (%s). Renamed to %s",
                reason,
                corrupt_path.name,
            )
        except OSError as exc:
            self.logger.warning(
                "Failed to rename corrupt tasks.json (%s): %s", reason, exc
            )
        self.state.tasks = []

    def _append_event(self, event: TraceEvent) -> None:
        self.state.events.append(event)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.__dict__) + "\n")

    def _trace(
        self,
        phase: str,
        req_id: str,
        duration_ms: int,
        tool: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> TraceEvent:
        event = TraceEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            phase=phase,
            req_id=req_id,
            duration_ms=duration_ms,
            tool=tool,
            args=_truncate_args(args),
            result=_truncate_result(result),
            error=error,
        )
        self._append_event(event)
        return event

    def _generate_plan(self, text: str) -> Dict[str, Any]:
        plan_id = str(uuid.uuid4())
        return {
            "plan_id": plan_id,
            "intent": text,
            "steps": [
                {
                    "id": "step-1",
                    "action": "analyze_request",
                    "note": "Interpret the user request and update tasks.",
                },
                {
                    "id": "step-2",
                    "action": "update_task_queue",
                    "note": "Create or revise tasks based on intent.",
                },
                {
                    "id": "step-3",
                    "action": "await_next_tick",
                    "note": "Pause until next cycle.",
                },
            ],
            "requires_clarification": False,
        }

    def _verbalize_plan(self, plan: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        prompt = (
            "You are Yesman. Only verbalize the given plan as a short status update. "
            "Do not propose new plans.\n\nPlan JSON:\n"
            + json.dumps(plan)
        )
        try:
            response = requests.post(
                "http://127.0.0.1:11434/api/generate",
                json={
                    "model": "llama3",
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", ""), None
        except requests.RequestException as exc:
            return "", f"Ollama error: {exc}"

    def handle_user_message(self, text: str) -> Tuple[Dict[str, Any], str]:
        req_id = str(uuid.uuid4())
        start = time.perf_counter()
        plan = self._generate_plan(text)
        self._trace(
            phase="planning",
            req_id=req_id,
            duration_ms=_elapsed_ms(start),
            tool="metta.plan",
            args={"text": text},
            result="plan_json_created",
        )
        llm_start = time.perf_counter()
        verbal, error = self._verbalize_plan(plan)
        if error:
            self.state.errors.append(error)
            verbal = (
                "LLM unavailable. Derived status from plan: intent='"
                + plan.get("intent", "")
                + "'."
            )
        self._trace(
            phase="verbalize",
            req_id=req_id,
            duration_ms=_elapsed_ms(llm_start),
            tool="ollama.verbalize",
            args={"model": "llama3"},
            result=verbal[:120] if verbal else "fallback_response",
            error=error,
        )
        self._update_tasks_from_intent(text)
        self._persist_tasks()
        return plan, verbal

    def _update_tasks_from_intent(self, text: str) -> None:
        task = TaskItem(
            id=str(uuid.uuid4()),
            title=text.strip()[:60] or "New task",
            status="pending",
            progress="0%",
            details="Auto-created from user message.",
        )
        self.state.tasks.append(task)

    def tick(self) -> None:
        req_id = str(uuid.uuid4())
        self.state.mode = "working"
        start = time.perf_counter()
        time.sleep(0.1)
        if self.state.tasks:
            task = self.state.tasks[0]
            task.status = "in_progress"
            task.progress = "50%"
        self._trace(
            phase="tick",
            req_id=req_id,
            duration_ms=_elapsed_ms(start),
            tool="runtime.tick",
            args={"autopilot": self.state.autopilot},
            result="tick_complete",
        )
        self._persist_tasks()
        self.state.mode = "idle"

    def set_autopilot(self, on: bool) -> None:
        self.state.autopilot = on

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {
            "tasks": [task.__dict__ for task in self.state.tasks],
            "events": [event.__dict__ for event in self.state.events[-200:]],
            "docs": self.state.docs,
            "errors": self.state.errors[-50:],
            "mode": self.state.mode,
            "autopilot": self.state.autopilot,
            "budgets": self.state.budgets,
        }

    def read_workspace_file(self, path: str) -> str:
        file_path = _safe_workspace_path(self.workspace_root, path)
        return file_path.read_text()

    def list_workspace_files(self) -> List[str]:
        workspace = self.workspace_root
        return [str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file()]


def _safe_workspace_path(workspace_root: Path, path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()
    if workspace_root not in resolved.parents and resolved != workspace_root:
        raise ValueError("Path outside workspace.")
    return resolved


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _truncate_args(args: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if args is None:
        return None
    truncated = {}
    for key, value in args.items():
        text = str(value)
        truncated[key] = text if len(text) < 200 else text[:200] + "..."
    return truncated


def _truncate_result(result: Optional[str]) -> Optional[str]:
    if result is None:
        return None
    return result if len(result) < 200 else result[:200] + "..."


def _parse_task_item(data: Dict[str, Any]) -> Optional[TaskItem]:
    if not {"id", "title", "status"}.issubset(data.keys()):
        return None
    return TaskItem(
        id=str(data.get("id")),
        title=str(data.get("title")),
        status=str(data.get("status")),
        progress=str(data.get("progress", "")),
        details=str(data.get("details", "")),
    )


def _setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("yesman")
    logger.setLevel(logging.INFO)
    if not any(
        isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(log_file)
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    return logger
