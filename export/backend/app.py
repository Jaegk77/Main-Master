from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_core import AgentRuntime

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "workspace"
STATE_DIR = WORKSPACE / "state"
LOG_DIR = WORKSPACE / "logs"
LOG_FILE = LOG_DIR / "yesman.log"

STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("yesman")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
logger.addHandler(handler)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"] ,
    allow_headers=["*"],
)

runtime = AgentRuntime.get(WORKSPACE)


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return runtime.get_state_snapshot()


@app.post("/api/chat")
def post_chat(payload: Dict[str, str]) -> Dict[str, Any]:
    text = payload.get("message", "")
    plan, verbal = runtime.handle_user_message(text)
    logger.info("chat handled: %s", text)
    return {"plan": plan, "assistant": verbal}


@app.post("/api/tick")
def post_tick() -> Dict[str, Any]:
    runtime.tick()
    logger.info("tick executed")
    return {"status": "ok"}


@app.post("/api/autopilot")
def post_autopilot(payload: Dict[str, Any]) -> Dict[str, Any]:
    on = bool(payload.get("on", False))
    runtime.set_autopilot(on)
    logger.info("autopilot set to %s", on)
    return {"autopilot": on}


@app.get("/api/logs/tail")
def get_logs_tail(n: int = 200) -> JSONResponse:
    if not LOG_FILE.exists():
        return JSONResponse({"lines": []})
    lines = LOG_FILE.read_text().splitlines()[-n:]
    return JSONResponse({"lines": lines})


@app.get("/api/workspace/list")
def list_workspace() -> JSONResponse:
    files = runtime.list_workspace_files()
    return JSONResponse({"files": files})


@app.get("/api/workspace/open")
def open_workspace(path: str) -> JSONResponse:
    try:
        content = runtime.read_workspace_file(path)
    except Exception as exc:  # noqa: BLE001
        logger.error("file read error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"path": path, "content": content})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            payload = json.loads(raw)
            msg_type = payload.get("type")
            if msg_type == "chat":
                text = payload.get("text", "")
                plan, verbal = runtime.handle_user_message(text)
                await ws.send_json({"type": "assistant", "text": verbal})
                await ws.send_json({"type": "plan", "json": plan})
            elif msg_type == "tick":
                runtime.tick()
            elif msg_type == "autopilot":
                runtime.set_autopilot(bool(payload.get("on")))
            elif msg_type == "read_file":
                path = payload.get("path", "")
                try:
                    content = runtime.read_workspace_file(path)
                    await ws.send_json(
                        {"type": "file", "path": path, "content": content}
                    )
                except Exception as exc:  # noqa: BLE001
                    runtime.state.errors.append(str(exc))
            snapshot = runtime.get_state_snapshot()
            await ws.send_json({"type": "tasks", "tasks": snapshot["tasks"], "mode": snapshot["mode"]})
            for event in snapshot["events"][-5:]:
                await ws.send_json({"type": "trace", "event": event})
            await ws.send_json({"type": "docs", "docs": snapshot["docs"]})
            await ws.send_json({"type": "errors", "errors": snapshot["errors"]})
    except WebSocketDisconnect:
        logger.info("websocket disconnected")

