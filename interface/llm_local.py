import json
import os
from typing import Any, Dict, List

import requests

SYSTEM_PROMPT = """You are a normalization engine. Output ONLY valid JSON.
Rules:
- "intent" MUST be a JSON string (not null, not object, not list)
- "actions", "constraints", "questions" MUST be arrays
- If the user requests building an app/UI/API/codebase, you MUST include write-file actions that fully create the required files.
- All paths MUST begin with: workspace/ui/
- Provide complete file contents (no placeholders) for:
  - workspace/ui/api.py (FastAPI server exposing POST /chat)
  - workspace/ui/web/index.html (simple chat UI)
  - workspace/ui/web/app.js (fetch calls to backend)
  - workspace/ui/requirements.txt (fastapi, uvicorn)
  - workspace/ui/README.md (commands to run)
- The API should import and reuse the existing ToolRunner from controller.py (do not duplicate logic).
  Example: from controller import ToolRunner
- The /chat endpoint accepts JSON: {"message": "..."} and returns {"assistant_says": "...", "assistant_json": "..."}
- The server must keep one ToolRunner instance alive (global singleton) so memory persists.
Example:
{"intent":"greeting","actions":[],"constraints":[],"questions":[],"raw":"Hello"}
Schema:
{
  "intent": "...",
  "actions": [
     {"tool":"fetch-web|fetch-url|write-file|apply-patch|run-tests|spawn-sandbox|evaluate-candidate|promote-candidate|need-data",
      "args":[...],
      "why":"..."}
  ],
  "constraints":[...],
  "questions":[...],
  "raw":"..."
}
"""

VERBALIZER_PROMPT = """You are the verbalizer for a local agent.
Never say “what do you want to talk about”.
Always respond as an agent:
- what it did (progress)
- what it’s doing next (next_actions)
- what it needs (questions only if blocked; max 2)
Do not invent actions not present in assistant-json.
If assistant-json has next_actions, describe them.
If blocked, ask only the top 1–2 questions.
Keep output concise and action-oriented.
"""

PLANNER_PROMPT = """You are a task planner. Output ONLY strict JSON array of steps.
Each step must be one of:
- {"type":"tool","tool":"write-file|apply-patch|fetch-web|fetch-url|run-tests|spawn-sandbox|evaluate-candidate|promote-candidate|need-data","args":[...],"why":"..."}
- {"type":"note","text":"..."}
Return [] if no steps are required.
"""

DEFAULT_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_TIMEOUT_S = 60


def _get_env(name: str, default: Any) -> Any:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _sanitize(norm: Dict[str, Any], raw: str) -> Dict[str, Any]:
    if not isinstance(norm, dict):
        norm = {}

    intent = norm.get("intent")
    if not isinstance(intent, str):
        intent = "unknown"

    actions_input = norm.get("actions")
    actions: List[Dict[str, Any]] = []
    if isinstance(actions_input, list):
        for item in actions_input:
            if not isinstance(item, dict):
                continue
            tool = item.get("tool")
            if not isinstance(tool, str):
                continue
            args_input = item.get("args")
            if isinstance(args_input, list):
                args = [str(arg) for arg in args_input]
            else:
                args = []
            why = item.get("why")
            why_str = why if isinstance(why, str) else ""
            actions.append({"tool": tool, "args": args, "why": why_str})

    constraints_input = norm.get("constraints")
    if isinstance(constraints_input, list):
        constraints = [str(item) for item in constraints_input]
    else:
        constraints = []

    questions_input = norm.get("questions")
    if isinstance(questions_input, list):
        questions = [str(item) for item in questions_input]
    else:
        questions = []

    return {
        "intent": intent,
        "actions": actions,
        "constraints": constraints,
        "questions": questions,
        "raw": str(raw),
    }


def normalize_user_message(text: str) -> Dict[str, Any]:
    url = _get_env("YESMAN_LLM_URL", DEFAULT_URL)
    model = _get_env("YESMAN_LLM_MODEL", DEFAULT_MODEL)
    timeout_s = int(_get_env("YESMAN_LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    response = requests.post(url, json=payload, timeout=timeout_s)
    response.raise_for_status()
    data = response.json()
    message = data.get("message", {})
    content = message.get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return _sanitize({}, text)
    return _sanitize(parsed, text)


def normalized_to_incoming_text(data: Dict[str, Any]) -> str:
    sanitized = _sanitize(data, data.get("raw", ""))
    return json.dumps(sanitized, separators=(",", ":"), ensure_ascii=False)


def verbalize_response(user_text: str, plan_json: str, tool_notes: List[str]) -> str:
    url = _get_env("YESMAN_LLM_URL", DEFAULT_URL)
    model = _get_env("YESMAN_LLM_MODEL", DEFAULT_MODEL)
    timeout_s = int(_get_env("YESMAN_LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    notes_section = "\n".join(f"- {note}" for note in tool_notes) if tool_notes else "None"
    user_prompt = (
        "User message:\n"
        f"{user_text}\n\n"
        "Plan JSON:\n"
        f"{plan_json}\n\n"
        "Tool notes:\n"
        f"{notes_section}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": VERBALIZER_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout_s)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str):
            return ""
        return content.strip()
    except Exception:
        return ""


def plan_next_steps(prompt: str) -> List[Dict[str, Any]]:
    url = _get_env("YESMAN_LLM_URL", DEFAULT_URL)
    model = _get_env("YESMAN_LLM_MODEL", DEFAULT_MODEL)
    timeout_s = int(_get_env("YESMAN_LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout_s)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "[]")
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []
    except Exception:
        return []
