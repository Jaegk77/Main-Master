"""Yesman controller setup:
1) Start Ollama: `ollama serve`
2) Pull a model: `ollama pull llama3.1:8b`
3) Run: `python controller.py`
4) In another terminal: `tail -f workspace/logs/yesman.log`
"""

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

import feedparser
import requests
from bs4 import BeautifulSoup
from hyperon import MeTTa

from interface.llm_local import (
    normalize_user_message,
    normalized_to_incoming_text,
    plan_next_steps,
    verbalize_response,
)
from interface.task_queue import (
    QueueState,
    add_task,
    advance_cursor,
    append_event,
    get_next_runnable_task,
    load_queue,
    mark_blocked,
    mark_done,
    mark_failed,
    mark_in_progress,
    save_queue,
)
from interface.task_templates import build_ui_task, debug_task, research_task

PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
MAIN_METTA_PATH = PROJECT_ROOT / "main_yesman.metta"

ALLOWED_PREFIXES = (
    "assistant-",
    "tool-",
    "doc",
    "request",
    "accepted",
    "memory-note",
    "tool-error",
    "candidate-score",
)

TOOL_NAMES = [
    "fetch-web",
    "fetch-url",
    "write-file",
    "apply-patch",
    "run-tests",
    "spawn-sandbox",
    "evaluate-candidate",
    "promote-candidate",
    "need-data",
]

TOOL_SIGNATURES = {
    "need-data": 2,
    "fetch-web": 3,
    "fetch-url": 2,
    "write-file": 3,
    "apply-patch": 3,
    "run-tests": 2,
    "spawn-sandbox": 2,
    "evaluate-candidate": 3,
    "promote-candidate": 2,
}

MAX_WEB_FETCHES_PER_LOOP = 3
MAX_LLM_ACTIONS_PER_LOOP = 5
MAX_AUTOPILOT_CYCLES = 3
MAX_TASK_STEPS_PER_TICK = 5
MAX_LLM_CALLS_PER_TICK = 2
MAX_TOOL_CALLS_PER_TICK = 5
MAX_DOWNLOAD_BYTES = 1_000_000
MAX_QUESTIONS_PER_TURN = 2
POLITE_DELAY_S = 0.5
AUTOPILOT_SLEEP_S = 0.5
TOOL_ERROR_LIMIT = 5


@dataclass
class ToolRequest:
    tool: str
    request_id: str
    args: List[Any]


class _DefaultLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "req_id"):
            record.req_id = "-"
        if not hasattr(record, "phase"):
            record.phase = "-"
        if not hasattr(record, "duration_ms"):
            record.duration_ms = "-"
        return True


def setup_logger() -> logging.Logger:
    log_dir = WORKSPACE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "yesman.log"
    logger = logging.getLogger("yesman")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        log_path, maxBytes=2 * 1024 * 1024, backupCount=5
    )
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s req_id=%(req_id)s phase=%(phase)s "
        "duration_ms=%(duration_ms)s %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(_DefaultLogFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    req_id: str,
    phase: str,
    message: str,
    duration_ms: Optional[float] = None,
) -> None:
    extra = {
        "req_id": req_id,
        "phase": phase,
        "duration_ms": f"{duration_ms:.2f}" if duration_ms is not None else "-",
    }
    logger.log(level, message, extra=extra)


def escape_metta_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def unescape_metta_string(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def extract_top_level_sexpressions(text: str) -> List[str]:
    expressions: List[str] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "(":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == ")":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    expressions.append(text[start : idx + 1])
                    start = None
    return expressions


def extract_list_children(expr: str) -> List[str]:
    stripped = expr.lstrip()
    if not stripped.startswith("(list"):
        return []
    start_idx = expr.find("(list")
    if start_idx == -1:
        return []
    children: List[str] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escape = False
    idx = start_idx
    while idx < len(expr):
        ch = expr[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            idx += 1
            continue
        if ch == '"':
            in_string = True
            idx += 1
            continue
        if ch == "(":
            if depth == 0:
                depth = 1
            else:
                if depth == 1:
                    start = idx
                depth += 1
        elif ch == ")":
            if depth > 0:
                depth -= 1
                if depth == 1 and start is not None:
                    children.append(expr[start : idx + 1])
                    start = None
                if depth == 0:
                    break
        idx += 1
    return children


def tokenize_sexpr(expr: str) -> List[Any]:
    tokens: List[Any] = []
    idx = 0
    while idx < len(expr):
        ch = expr[idx]
        if ch.isspace():
            idx += 1
            continue
        if ch in ("(", ")"):
            tokens.append(ch)
            idx += 1
            continue
        if ch == '"':
            idx += 1
            value_chars: List[str] = []
            escape = False
            while idx < len(expr):
                current = expr[idx]
                if escape:
                    value_chars.append(current)
                    escape = False
                elif current == "\\":
                    escape = True
                elif current == '"':
                    break
                else:
                    value_chars.append(current)
                idx += 1
            tokens.append(("string", "".join(value_chars)))
            idx += 1
            continue
        start = idx
        while idx < len(expr) and not expr[idx].isspace() and expr[idx] not in ("(", ")"):
            idx += 1
        symbol = expr[start:idx]
        tokens.append(("symbol", symbol))
    return tokens


def _convert_symbol(value: str) -> Any:
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_sexpr(expr: str) -> Any:
    tokens = tokenize_sexpr(expr)
    stack: List[List[Any]] = []
    current: List[Any] = []
    for tok in tokens:
        if tok == "(":
            stack.append(current)
            current = []
        elif tok == ")":
            if not stack:
                raise ValueError("Unbalanced parentheses")
            completed = current
            current = stack.pop()
            current.append(completed)
        else:
            tok_type, value = tok
            if tok_type == "string":
                current.append(value)
            else:
                current.append(_convert_symbol(value))
    if stack:
        raise ValueError("Unbalanced parentheses")
    if len(current) == 1:
        return current[0]
    return current


def head_symbol(expr: Any) -> str:
    if isinstance(expr, list) and expr:
        head = expr[0]
        if isinstance(head, str):
            return head
    return ""


def is_allowed_head(symbol: str) -> bool:
    return any(symbol.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def is_safe_workspace_path(path_str: str) -> bool:
    if os.path.isabs(path_str):
        return False
    posix_path = PurePosixPath(path_str)
    if ".." in posix_path.parts:
        return False
    if posix_path.parts and posix_path.parts[0] != "workspace":
        return False
    if "main_yesman.metta" in posix_path.parts:
        return False
    return True


def ensure_workspace_path(path_str: str) -> Path:
    if not is_safe_workspace_path(path_str):
        raise ValueError("Unsafe path; only workspace/ relative paths are allowed")
    return PROJECT_ROOT / path_str


def parse_tool_expression(expr: str) -> Optional[ToolRequest]:
    try:
        parsed = parse_sexpr(expr)
    except ValueError:
        return None
    if not isinstance(parsed, list) or len(parsed) < 2:
        return None
    tool_name = parsed[0]
    if not isinstance(tool_name, str) or not tool_name.startswith("tool-"):
        return None
    request_id = str(parsed[1])
    args = parsed[2:]
    return ToolRequest(tool=tool_name.replace("tool-", ""), request_id=request_id, args=args)


def clean_text(text: str) -> str:
    return " ".join(text.split())


class ToolRunner:
    def __init__(self, logger: logging.Logger, trace_enabled: bool = False) -> None:
        self.metta = MeTTa()
        self.sandboxes: Dict[str, MeTTa] = {}
        if MAIN_METTA_PATH.exists():
            self.metta.run(MAIN_METTA_PATH.read_text())
        self._request_id = 0
        self.logger = logger
        self.trace_enabled = trace_enabled

    def set_trace(self, enabled: bool) -> None:
        self.trace_enabled = enabled

    def trace(self, req_id: str, message: str) -> None:
        log_event(self.logger, logging.INFO, req_id, "trace", message)
        if self.trace_enabled:
            print(f"[trace] {message}")

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def next_request_id(self) -> str:
        self._request_id += 1
        return str(self._request_id)

    def run_main_step(self, incoming: str, req_id: str, now: str) -> List[Any]:
        incoming_literal = escape_metta_string(incoming)
        req_literal = escape_metta_string(req_id)
        now_literal = escape_metta_string(now)
        expr = (
            f"!(main-step (incoming {incoming_literal}) (next-id {req_literal}) (now {now_literal}))"
        )
        results = self.metta.run(expr)
        self.assert_returned_atoms(results)
        return list(results)

    def run_agent_tick(self, now: str) -> List[Any]:
        now_literal = escape_metta_string(now)
        expr = f"!(agent-tick (now {now_literal}))"
        results = self.metta.run(expr)
        self.assert_returned_atoms(results)
        return list(results)

    def assert_returned_atoms(self, results: Iterable[Any]) -> None:
        for item in results:
            text = str(item)
            for expr in extract_top_level_sexpressions(text):
                try:
                    parsed = parse_sexpr(expr)
                except ValueError:
                    continue
                symbol = head_symbol(parsed)
                if expr.lstrip().startswith("(list"):
                    children = extract_list_children(expr)
                    for child in children:
                        try:
                            child_parsed = parse_sexpr(child)
                        except ValueError:
                            continue
                        child_symbol = head_symbol(child_parsed)
                        if child_symbol and is_allowed_head(child_symbol):
                            self.metta.run(child)
                elif symbol and is_allowed_head(symbol):
                    self.metta.run(expr)

    def collect_tool_requests(self) -> List[ToolRequest]:
        requests: List[ToolRequest] = []
        seen: set = set()
        for tool in TOOL_NAMES:
            for arity in range(1, 5):
                vars_list = " ".join(f"$a{idx}" for idx in range(1, arity + 1))
                pattern = f"(tool-{tool} $id {vars_list})" if vars_list else f"(tool-{tool} $id)"
                query = f"!(match &self {pattern} {pattern})"
                matches = self.metta.run(query)
                for match in matches:
                    for expr in extract_top_level_sexpressions(str(match)):
                        if expr in seen:
                            continue
                        seen.add(expr)
                        tool_request = parse_tool_expression(expr)
                        if tool_request:
                            requests.append(tool_request)
        return requests

    def inject_doc(self, source: str, title: str, url: str, text: str) -> None:
        doc_id = f"doc-{self.next_request_id()}"
        ts = self.now_iso()
        expr = (
            f"(doc {escape_metta_string(doc_id)} {escape_metta_string(source)} "
            f"{escape_metta_string(title)} {escape_metta_string(url)} "
            f"{escape_metta_string(ts)} {escape_metta_string(text)})"
        )
        self.metta.run(expr)

    def inject_tool_error(self, tool: str, message: str) -> None:
        ts = self.now_iso()
        log_event(self.logger, logging.ERROR, "-", "tool-error", f"{tool}: {message}")
        expr = (
            f"(tool-error {escape_metta_string(tool)} {escape_metta_string(message)} "
            f"{escape_metta_string(ts)})"
        )
        self.metta.run(expr)

    def inject_memory_note(self, req_id: str, normalized_json: str) -> None:
        ts = self.now_iso()
        expr = (
            f"(memory-note {escape_metta_string(f'llm:{req_id}')} "
            f"{escape_metta_string(normalized_json)} {escape_metta_string(ts)})"
        )
        self.metta.run(expr)

    def inject_llm_actions(self, req_id: str, normalized: Dict[str, Any], ts: str) -> None:
        actions_input = normalized.get("actions")
        if not isinstance(actions_input, list):
            self.inject_tool_error("llm-action", "Actions missing or not a list")
            self.trace(req_id, "llm-action invalid actions list")
            actions_input = []

        injected_actions: List[Dict[str, Any]] = []
        count = 0
        for action in actions_input:
            if count >= MAX_LLM_ACTIONS_PER_LOOP:
                log_event(
                    self.logger,
                    logging.WARNING,
                    req_id,
                    "llm-action",
                    "ACTION_LIMIT_REACHED",
                )
                self.inject_tool_error("llm-action", "LLM action limit reached")
                break
            if not isinstance(action, dict):
                log_event(
                    self.logger,
                    logging.WARNING,
                    req_id,
                    "llm-action",
                    "Action is not an object",
                )
                self.inject_tool_error("llm-action", "Action is not an object")
                continue
            tool = action.get("tool")
            if not isinstance(tool, str):
                log_event(
                    self.logger,
                    logging.WARNING,
                    req_id,
                    "llm-action",
                    "Tool name is not a string",
                )
                self.inject_tool_error("llm-action", "Tool name is not a string")
                continue
            if tool not in TOOL_NAMES or tool not in TOOL_SIGNATURES:
                log_event(
                    self.logger,
                    logging.WARNING,
                    req_id,
                    "llm-action",
                    f"Unknown tool requested: {tool}",
                )
                self.inject_tool_error("llm-action", f"Unknown tool requested: {tool}")
                continue
            why = action.get("why")
            why_str = why if isinstance(why, str) else ""
            args_input = action.get("args")
            args = args_input if isinstance(args_input, list) else []
            built = self._build_tool_atom(tool, args, why_str)
            if built is None:
                log_event(
                    self.logger,
                    logging.WARNING,
                    req_id,
                    "llm-action",
                    f"Failed to build tool atom: {tool}",
                )
                self.inject_tool_error("llm-action", f"Failed to build tool atom: {tool}")
                continue
            atom, sanitized_args, effective_why = built
            self.metta.run(atom)
            self.trace(req_id, f"llm-action injected {tool}")
            injected_actions.append(
                {"tool": tool, "args": sanitized_args, "why": effective_why}
            )
            count += 1

        compact_actions = json.dumps(injected_actions, separators=(",", ":"), ensure_ascii=False)
        expr = (
            f"(memory-note {escape_metta_string(f'llm-actions:{req_id}')} "
            f"{escape_metta_string(compact_actions)} {escape_metta_string(ts)})"
        )
        self.metta.run(expr)

    def _build_tool_atom(
        self, tool: str, args: List[Any], why: str
    ) -> Optional[Tuple[str, List[Any], str]]:
        if tool == "fetch-web":
            query = str(args[0]) if len(args) > 0 else ""
            why_str = str(args[1]) if len(args) > 1 and str(args[1]) else why
            if not why_str:
                why_str = "LLM requested fetch-web"
            limit_raw = args[2] if len(args) > 2 else 3
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError):
                limit = 3
            atom = f"({tool} {escape_metta_string(query)} {escape_metta_string(why_str)} {limit})"
            return atom, [query, why_str, limit], why_str
        if tool == "need-data":
            topic = str(args[0]) if len(args) > 0 else "unknown"
            why_str = str(args[1]) if len(args) > 1 and str(args[1]) else why
            if not why_str:
                why_str = "LLM requested need-data"
            atom = f"({tool} {escape_metta_string(topic)} {escape_metta_string(why_str)})"
            return atom, [topic, why_str], why_str
        if tool in ("fetch-url", "run-tests", "spawn-sandbox", "promote-candidate"):
            first = str(args[0]) if len(args) > 0 else ""
            why_str = str(args[1]) if len(args) > 1 and str(args[1]) else why
            if not why_str:
                why_str = f"LLM requested {tool}"
            atom = f"({tool} {escape_metta_string(first)} {escape_metta_string(why_str)})"
            return atom, [first, why_str], why_str
        if tool in ("write-file", "apply-patch", "evaluate-candidate"):
            first = str(args[0]) if len(args) > 0 else ""
            second = str(args[1]) if len(args) > 1 else ""
            why_str = str(args[2]) if len(args) > 2 and str(args[2]) else why
            if not why_str:
                why_str = f"LLM requested {tool}"
            atom = (
                f"({tool} {escape_metta_string(first)} {escape_metta_string(second)} "
                f"{escape_metta_string(why_str)})"
            )
            return atom, [first, second, why_str], why_str
        return None

    def handle_tool_requests(self, tool_requests: List[ToolRequest]) -> None:
        count = 0
        for req in tool_requests:
            if req.tool not in TOOL_NAMES:
                log_event(
                    self.logger,
                    logging.WARNING,
                    req.request_id,
                    "tool",
                    f"Unknown tool request: {req.tool}",
                )
                self.inject_tool_error(req.tool, "Unknown tool request")
                continue
            if req.tool == "fetch-web":
                count += 1
                if count > MAX_WEB_FETCHES_PER_LOOP:
                    log_event(
                        self.logger,
                        logging.WARNING,
                        req.request_id,
                        "tool",
                        "TOOL_LIMIT_REACHED",
                    )
                    self.inject_tool_error(req.tool, "Fetch limit reached")
                    continue
                self._handle_fetch_web(req)
            elif req.tool == "fetch-url":
                self._handle_fetch_url(req)
            elif req.tool == "write-file":
                self._handle_write_file(req)
            elif req.tool == "apply-patch":
                self._handle_apply_patch(req)
            elif req.tool == "run-tests":
                self._handle_run_tests(req)
            elif req.tool == "spawn-sandbox":
                self._handle_spawn_sandbox(req)
            elif req.tool == "evaluate-candidate":
                self._handle_evaluate_candidate(req)
            elif req.tool == "promote-candidate":
                self._handle_promote_candidate(req)
            elif req.tool == "need-data":
                self._handle_need_data(req)
            self.trace(req.request_id, f"tool {req.tool} handled")

    def _handle_fetch_web(self, req: ToolRequest) -> None:
        try:
            query = str(req.args[0]) if len(req.args) > 0 else ""
            why = str(req.args[1]) if len(req.args) > 1 else ""
            limit = int(req.args[2]) if len(req.args) > 2 else 5
            keywords = [word.lower() for word in query.split() if word.strip()]
            url = "https://news.google.com/rss/search?q=" + requests.utils.quote(query)
            time.sleep(POLITE_DELAY_S)
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                combined = f"{title} {summary}".lower()
                if keywords and not any(keyword in combined for keyword in keywords):
                    continue
                text = clean_text(summary)
                if why:
                    text = f"{text}\n\nWhy: {why}"
                self.inject_doc("fetch-web", title, link, text)
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_fetch_url(self, req: ToolRequest) -> None:
        try:
            url = str(req.args[0]) if len(req.args) > 0 else ""
            why = str(req.args[1]) if len(req.args) > 1 else ""
            time.sleep(POLITE_DELAY_S)
            response = requests.get(url, stream=True, timeout=20)
            response.raise_for_status()
            content = response.raw.read(MAX_DOWNLOAD_BYTES, decode_content=True)
            soup = BeautifulSoup(content, "html.parser")
            text = clean_text(soup.get_text(" "))
            if why:
                text = f"{text}\n\nWhy: {why}"
            title = soup.title.text.strip() if soup.title else url
            self.inject_doc("fetch-url", title, url, text)
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_write_file(self, req: ToolRequest) -> None:
        try:
            path = str(req.args[0]) if len(req.args) > 0 else ""
            content = str(req.args[1]) if len(req.args) > 1 else ""
            _ = str(req.args[2]) if len(req.args) > 2 else ""
            target = ensure_workspace_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            log_event(self.logger, logging.INFO, req.request_id, "tool", f"write-file {path} bytes={len(content)}")
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_apply_patch(self, req: ToolRequest) -> None:
        try:
            path = str(req.args[0]) if len(req.args) > 0 else ""
            diff = str(req.args[1]) if len(req.args) > 1 else ""
            _ = str(req.args[2]) if len(req.args) > 2 else ""
            if not is_safe_workspace_path(path):
                raise ValueError("Unsafe patch path")
            if "main_yesman.metta" in diff:
                raise ValueError("Refusing to patch main_yesman.metta")
            for line in diff.splitlines():
                if line.startswith("+++") or line.startswith("---"):
                    parts = line.split()
                    if len(parts) >= 2:
                        file_path = parts[1]
                        file_path = file_path.removeprefix("a/").removeprefix("b/")
                        if not is_safe_workspace_path(file_path):
                            raise ValueError("Patch contains unsafe path")
            patch_path = ensure_workspace_path(path)
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text(diff)
            result = os.system(f"git -C {PROJECT_ROOT} apply {patch_path}")
            if result != 0:
                raise RuntimeError("git apply failed")
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_run_tests(self, req: ToolRequest) -> None:
        try:
            suite = str(req.args[0]) if len(req.args) > 0 else "default"
            ts = self.now_iso()
            expr = (
                f"(candidate-score {escape_metta_string(suite)} 0.5 "
                f"{escape_metta_string(ts)})"
            )
            self.metta.run(expr)
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_spawn_sandbox(self, req: ToolRequest) -> None:
        try:
            name = str(req.args[0]) if len(req.args) > 0 else "sandbox"
            if name not in self.sandboxes:
                self.sandboxes[name] = MeTTa()
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_evaluate_candidate(self, req: ToolRequest) -> None:
        try:
            metric = str(req.args[1]) if len(req.args) > 1 else "default"
            ts = self.now_iso()
            expr = (
                f"(candidate-score {escape_metta_string(metric)} 0.7 "
                f"{escape_metta_string(ts)})"
            )
            self.metta.run(expr)
        except Exception as exc:
            self.inject_tool_error(req.tool, str(exc))

    def _handle_promote_candidate(self, req: ToolRequest) -> None:
        _ = req
        return

    def _handle_need_data(self, req: ToolRequest) -> None:
        _ = req
        return

    def get_assistant_outputs(self) -> Tuple[List[str], List[str]]:
        says_results = self.metta.run("!(match &self (assistant-says $id $val) $val)")
        json_results = self.metta.run("!(match &self (assistant-json $id $val) $val)")
        return (
            self._resolve_output_values(says_results),
            self._resolve_output_values(json_results),
        )

    def get_recent_tool_errors(self, limit: int = TOOL_ERROR_LIMIT) -> List[str]:
        error_results = self.metta.run(
            "!(match &self (tool-error $tool $message $ts) $message)"
        )
        notes = self._resolve_output_values(error_results)
        if len(notes) > limit:
            return notes[-limit:]
        return notes

    def _resolve_output_values(self, results: Iterable[Any]) -> List[str]:
        outputs: List[str] = []
        for result in results:
            value = str(result)
            for expr in extract_top_level_sexpressions(value):
                evaluated = self._evaluate_expression(expr)
                if evaluated is not None:
                    outputs.append(evaluated)
            if not extract_top_level_sexpressions(value):
                outputs.append(unescape_metta_string(value))
        return outputs

    def _evaluate_expression(self, expr: str) -> Optional[str]:
        try:
            evaluated = self.metta.run(expr)
        except Exception:
            return None
        if not evaluated:
            return None
        first = str(evaluated[0])
        return unescape_metta_string(first)


try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    RICH_AVAILABLE = True
    console = Console()
except Exception:
    RICH_AVAILABLE = False
    console = None


def print_help() -> None:
    print("Commands:")
    print("  /help - show this help")
    print("  /tools - list tool names")
    print("  /files - list workspace files")
    print("  /open <path> - open a workspace file")
    print("  /docs - list docs in memory")
    print("  /candidates - list candidate scores")
    print("  /plan - show latest assistant plan JSON")
    print("  /raw - show latest assistant-says atoms")
    print("  /tasks - list tasks")
    print("  /task <id> - show task details")
    print("  /cancel <id> - cancel task")
    print("  /trace on|off - toggle trace output")
    print("  /autopilot on|off - toggle autonomous cycles")
    print("  /tick - run one autonomous cycle")
    print("  /ui on|off - toggle dashboard")
    print("  /debugstep <text> - run main-step and show raw results")
    print("  /quit - exit")
    print("  tail -f workspace/logs/yesman.log")


def list_workspace_files() -> None:
    if not WORKSPACE_DIR.exists():
        print("workspace/ does not exist")
        return
    for path in WORKSPACE_DIR.rglob("*"):
        if path.is_file():
            relative = path.relative_to(PROJECT_ROOT)
            print(relative)


def open_workspace_file(path_str: str) -> None:
    try:
        path = ensure_workspace_path(path_str)
        if not path.exists():
            print("File not found")
            return
        print(path.read_text())
    except Exception as exc:
        print(f"Error: {exc}")


def list_docs(runner: ToolRunner) -> None:
    results = runner.metta.run(
        "!(match &self (doc $id $source $title $url $ts $text) (list $id $title $url))"
    )
    for item in results:
        print(item)


def list_candidates(runner: ToolRunner) -> None:
    results = runner.metta.run(
        "!(match &self (candidate-score $metric $score $ts) (list $metric $score $ts))"
    )
    for item in results:
        print(item)


def list_tasks(state: QueueState) -> None:
    for task in state.tasks:
        print(
            f"{task.get('id')} {task.get('status')} "
            f"{task.get('cursor')}/{len(task.get('steps', []))} "
            f"{task.get('title')} {task.get('next_run_ts')}"
        )


def show_task(state: QueueState, task_id: str) -> None:
    for task in state.tasks:
        if task.get("id") == task_id:
            print(json.dumps(task, indent=2, ensure_ascii=False))
            return
    print("Task not found")


def cancel_task(state: QueueState, task_id: str) -> None:
    mark_failed(state, task_id, "canceled")


def task_snapshot(state: QueueState) -> str:
    active = None
    for task in state.tasks:
        if task.get("status") == "in_progress":
            active = task
            break
    if not active:
        return "No active task."
    return (
        f"{active.get('id')} {active.get('title')} "
        f"status={active.get('status')} cursor={active.get('cursor')}"
    )


def ensure_plan_schema(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "goal": plan.get("goal", "Assist user request"),
        "plan": plan.get("plan", [])[:6],
        "next_actions": plan.get("next_actions", [])[:3],
        "assumptions": plan.get("assumptions", []),
        "questions": plan.get("questions", [])[:MAX_QUESTIONS_PER_TURN],
        "progress": plan.get("progress", []),
        "status": plan.get("status", "idle"),
    }


def render_dashboard(state: QueueState, last_action: str, last_error: str, timings: Dict[str, float]) -> None:
    if not RICH_AVAILABLE:
        return
    table = Table(title="Yesman")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Active Task", task_snapshot(state))
    table.add_row("Last Action", last_action)
    table.add_row("Last Error", last_error)
    for key, value in timings.items():
        table.add_row(key, f"{value:.2f}ms")
    console.print(Panel(table))


def route_task_from_text(state: QueueState, text: str) -> Optional[Dict[str, Any]]:
    lower = text.lower()
    if "build" in lower and "interface" in lower:
        template = build_ui_task()
    elif "research" in lower:
        template = research_task(text)
    elif "debug" in lower:
        template = debug_task(text)
    else:
        return None
    return add_task(
        state,
        title=template["title"],
        goal=template["goal"],
        steps=template["steps"],
        priority=template.get("priority", 50),
    )


def execute_task_step(
    runner: ToolRunner,
    task: Dict[str, Any],
    step: Dict[str, Any],
    budgets: Dict[str, int],
) -> bool:
    step_type = step.get("type")
    task_id = task.get("id", "-")
    if step_type == "tool":
        if budgets["tool_calls"] >= MAX_TOOL_CALLS_PER_TICK:
            log_event(runner.logger, logging.WARNING, task_id, "task_step_execute", "TOOL_LIMIT_REACHED")
            runner.trace(task_id, "tool limit reached")
            return False
        tool = step.get("tool")
        args = step.get("args", [])
        if tool not in TOOL_NAMES or tool not in TOOL_SIGNATURES:
            log_event(runner.logger, logging.WARNING, task_id, "task_step_execute", f"Unknown tool {tool}")
            runner.inject_tool_error("task-step", f"Unknown tool {tool}")
            return False
        if not isinstance(args, list):
            args = []
        req = ToolRequest(tool=tool, request_id=str(task_id), args=args)
        runner.handle_tool_requests([req])
        budgets["tool_calls"] += 1
        return True
    if step_type == "llm":
        if budgets["llm_calls"] >= MAX_LLM_CALLS_PER_TICK:
            log_event(runner.logger, logging.WARNING, task_id, "task_step_execute", "LLM_CALL_LIMIT_REACHED")
            runner.trace(task_id, "llm call limit reached")
            return False
        prompt = str(step.get("input", ""))
        new_steps = plan_next_steps(prompt)
        if new_steps:
            task_steps = task.get("steps", [])
            task_steps.extend(new_steps)
            task["steps"] = task_steps
        budgets["llm_calls"] += 1
        return True
    if step_type == "evaluate":
        kind = step.get("kind")
        if kind == "ui_build_check":
            required = [
                "workspace/ui/api.py",
                "workspace/ui/web/index.html",
                "workspace/ui/web/app.js",
                "workspace/ui/README.md",
                "workspace/ui/requirements.txt",
            ]
            missing = [path for path in required if not (PROJECT_ROOT / path).exists()]
            if missing:
                task["last_error"] = f"Missing files: {', '.join(missing)}"
                return False
            return True
        return True
    if step_type == "note":
        return True
    return False


def run_tick(
    runner: ToolRunner,
    state: QueueState,
    now_ts: str,
) -> None:
    tick_start = time.perf_counter()
    task = get_next_runnable_task(state, now_ts)
    if not task:
        log_event(runner.logger, logging.INFO, "-", "task_queue_select", "No runnable tasks")
        return
    task_id = task.get("id", "-")
    log_event(runner.logger, logging.INFO, task_id, "task_queue_select", task.get("title", ""))
    if task.get("status") == "pending":
        mark_in_progress(state, task_id)
    budgets = {"steps": 0, "tool_calls": 0, "llm_calls": 0}
    cursor = int(task.get("cursor", 0))
    steps = task.get("steps", [])
    while cursor < len(steps) and budgets["steps"] < MAX_TASK_STEPS_PER_TICK:
        step = steps[cursor]
        start = time.perf_counter()
        ok = execute_task_step(runner, task, step, budgets)
        duration_ms = (time.perf_counter() - start) * 1000
        log_event(runner.logger, logging.INFO, task_id, "task_step_execute", step.get("type", ""), duration_ms)
        if not ok:
            task["next_run_ts"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
            log_event(runner.logger, logging.WARNING, task_id, "task_step_execute", "RETRY_SCHEDULED")
            break
        advance_cursor(state, task_id)
        cursor += 1
        budgets["steps"] += 1
    if cursor >= len(steps):
        mark_done(state, task_id)
    else:
        if budgets["steps"] >= MAX_TASK_STEPS_PER_TICK:
            log_event(
                runner.logger,
                logging.WARNING,
                task_id,
                "task_step_execute",
                "ACTION_LIMIT_REACHED",
            )
        task["next_run_ts"] = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    save_queue(state)
    tick_total = (time.perf_counter() - tick_start) * 1000
    log_event(runner.logger, logging.INFO, task_id, "tick_total", "tick complete", tick_total)


def run_autonomous_cycles(runner: ToolRunner, state: QueueState, max_cycles: int) -> None:
    for cycle_index in range(max_cycles):
        now_ts = datetime.now(timezone.utc).isoformat()
        run_tick(runner, state, now_ts)
        if cycle_index < max_cycles - 1:
            time.sleep(AUTOPILOT_SLEEP_S)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--autopilot", action="store_true")
    args = parser.parse_args()

    logger = setup_logger()
    runner = ToolRunner(logger)
    state = load_queue()
    if not state.tasks:
        template = build_ui_task()
        add_task(
            state,
            title=template["title"],
            goal=template["goal"],
            steps=template["steps"],
            priority=template["priority"],
        )
    save_queue(state)

    autopilot_enabled = False
    trace_enabled = False
    ui_enabled = False
    last_action = ""
    last_error = ""
    print("Yesman controller ready. Type /help for commands.")

    if args.autopilot:
        autopilot_enabled = True
        print("Autopilot: on")
        while True:
            now_ts = datetime.now(timezone.utc).isoformat()
            run_tick(runner, state, now_ts)
            time.sleep(1)

    while True:
        try:
            user_input = input("> ").strip()
        except EOFError:
            break
        if not user_input:
            continue
        req_id = runner.next_request_id()
        log_event(logger, logging.INFO, req_id, "input", user_input)
        user_start = time.perf_counter()
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            command = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            if command == "/help":
                print_help()
            elif command == "/tools":
                print("\n".join(TOOL_NAMES))
            elif command == "/files":
                list_workspace_files()
            elif command == "/open":
                if not arg:
                    print("Usage: /open workspace/<path>")
                else:
                    open_workspace_file(arg)
            elif command == "/docs":
                list_docs(runner)
            elif command == "/candidates":
                list_candidates(runner)
            elif command == "/plan":
                _, json_outputs = runner.get_assistant_outputs()
                if json_outputs:
                    print(json_outputs[-1])
                else:
                    print("No plan available.")
            elif command == "/raw":
                says_results = runner.metta.run(
                    "!(match &self (assistant-says $id $val) $val)"
                )
                if not says_results:
                    print("No assistant-says available.")
                else:
                    for item in says_results:
                        print(item)
            elif command == "/tasks":
                list_tasks(state)
            elif command == "/task":
                if not arg:
                    print("Usage: /task <id>")
                else:
                    show_task(state, arg)
            elif command == "/cancel":
                if not arg:
                    print("Usage: /cancel <id>")
                else:
                    cancel_task(state, arg)
                    save_queue(state)
            elif command == "/trace":
                if arg not in ("on", "off"):
                    print("Usage: /trace on|off")
                else:
                    trace_enabled = arg == "on"
                    runner.set_trace(trace_enabled)
                    print(f"Trace: {'on' if trace_enabled else 'off'}")
            elif command == "/autopilot":
                if arg not in ("on", "off"):
                    print("Usage: /autopilot on|off")
                else:
                    autopilot_enabled = arg == "on"
                    print(f"Autopilot: {'on' if autopilot_enabled else 'off'}")
            elif command == "/tick":
                now_ts = datetime.now(timezone.utc).isoformat()
                run_tick(runner, state, now_ts)
            elif command == "/ui":
                if arg not in ("on", "off"):
                    print("Usage: /ui on|off")
                else:
                    ui_enabled = arg == "on"
                    print(f"UI: {'on' if ui_enabled else 'off'}")
            elif command == "/debugstep":
                if not arg:
                    print("Usage: /debugstep <text>")
                else:
                    now = runner.now_iso()
                    results = runner.run_main_step(arg, req_id, now)
                    print("RAW main-step results:")
                    for item in results:
                        print(item)
                    print("LATEST assistant-says values:")
                    says_results = runner.metta.run(
                        "!(match &self (assistant-says $id $val) $val)"
                    )
                    for item in says_results:
                        print(item)
                    print("LATEST assistant-json values:")
                    json_results = runner.metta.run(
                        "!(match &self (assistant-json $id $val) $val)"
                    )
                    for item in json_results:
                        print(item)
            elif command == "/quit":
                break
            else:
                print("Unknown command. Type /help")
            continue

        routed = route_task_from_text(state, user_input)
        if routed:
            save_queue(state)

        now = runner.now_iso()
        normalize_start = time.perf_counter()
        try:
            normalized = normalize_user_message(user_input)
        except Exception as exc:
            logger.exception("Normalization failed", extra={"req_id": req_id, "phase": "normalize"})
            runner.inject_tool_error("normalize", str(exc))
            print(f"Normalization error: {exc}")
            continue
        normalize_ms = (time.perf_counter() - normalize_start) * 1000
        runner.trace(req_id, f"normalize {normalize_ms:.2f}ms")
        log_event(logger, logging.INFO, req_id, "normalize", "normalize complete", normalize_ms)
        llm_incoming = normalized.get("raw", user_input) or user_input
        llm_incoming_json = normalized_to_incoming_text(normalized)
        runner.inject_memory_note(req_id, llm_incoming_json)
        runner.inject_llm_actions(req_id, normalized, now)

        metta1_start = time.perf_counter()
        runner.run_main_step(llm_incoming, req_id, now)
        metta_step1_ms = (time.perf_counter() - metta1_start) * 1000
        runner.trace(req_id, f"metta_step1 {metta_step1_ms:.2f}ms")
        log_event(
            logger, logging.INFO, req_id, "metta_step1", "metta step1 complete", metta_step1_ms
        )

        collect_start = time.perf_counter()
        tool_requests = runner.collect_tool_requests()
        collect_tools_ms = (time.perf_counter() - collect_start) * 1000
        runner.trace(req_id, f"collect_tools {collect_tools_ms:.2f}ms")
        log_event(
            logger,
            logging.INFO,
            req_id,
            "collect_tools",
            "collect tools complete",
            collect_tools_ms,
        )

        handle_start = time.perf_counter()
        runner.handle_tool_requests(tool_requests)
        handle_tools_ms = (time.perf_counter() - handle_start) * 1000
        runner.trace(req_id, f"handle_tools {handle_tools_ms:.2f}ms")
        log_event(
            logger,
            logging.INFO,
            req_id,
            "handle_tools",
            "handle tools complete",
            handle_tools_ms,
        )

        metta2_start = time.perf_counter()
        runner.run_main_step(llm_incoming, req_id, now)
        metta_step2_ms = (time.perf_counter() - metta2_start) * 1000
        runner.trace(req_id, f"metta_step2 {metta_step2_ms:.2f}ms")
        log_event(
            logger, logging.INFO, req_id, "metta_step2", "metta step2 complete", metta_step2_ms
        )

        _, json_outputs = runner.get_assistant_outputs()
        plan_json = json_outputs[-1] if json_outputs else "{}"
        try:
            plan_data = ensure_plan_schema(json.loads(plan_json))
        except Exception:
            plan_data = ensure_plan_schema({})

        tool_notes = runner.get_recent_tool_errors()
        tool_notes.append(f"Task status: {task_snapshot(state)}")

        verbalize_start = time.perf_counter()
        try:
            response_text = verbalize_response(user_input, json.dumps(plan_data), tool_notes)
        except Exception as exc:
            logger.exception("Verbalizer failed", extra={"req_id": req_id, "phase": "verbalize"})
            runner.inject_tool_error("verbalize", str(exc))
            response_text = ""
        verbalize_ms = (time.perf_counter() - verbalize_start) * 1000
        runner.trace(req_id, f"verbalize {verbalize_ms:.2f}ms")
        log_event(
            logger, logging.INFO, req_id, "verbalize", "verbalize complete", verbalize_ms
        )

        if response_text:
            print(response_text)
        else:
            print(json.dumps(plan_data, ensure_ascii=False))

        user_total_ms = (time.perf_counter() - user_start) * 1000
        print(f"[Response] {user_total_ms / 1000:.2f} seconds")
        log_event(
            logger,
            logging.INFO,
            req_id,
            "user_turn_total",
            "normalize={:.2f}ms metta_step1={:.2f}ms collect_tools={:.2f}ms "
            "handle_tools={:.2f}ms metta_step2={:.2f}ms verbalize={:.2f}ms total={:.2f}ms".format(
                normalize_ms,
                metta_step1_ms,
                collect_tools_ms,
                handle_tools_ms,
                metta_step2_ms,
                verbalize_ms,
                user_total_ms,
            ),
            user_total_ms,
        )

        timings = {
            "normalize": normalize_ms,
            "metta_step1": metta_step1_ms,
            "collect_tools": collect_tools_ms,
            "handle_tools": handle_tools_ms,
            "metta_step2": metta_step2_ms,
            "verbalize": verbalize_ms,
            "total": user_total_ms,
        }
        if ui_enabled:
            render_dashboard(state, last_action, last_error, timings)

        if autopilot_enabled:
            run_autonomous_cycles(runner, state, MAX_AUTOPILOT_CYCLES)


if __name__ == "__main__":
    main()
