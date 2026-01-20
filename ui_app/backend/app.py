from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List

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
if not any(isinstance(existing, RotatingFileHandler) for existing in logger.handlers):
    logger.addHandler(handler)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

runtime = AgentRuntime.get(WORKSPACE)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_json(self, websocket: WebSocket, payload: Dict[str, Any]) -> None:
        await websocket.send_json(payload)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(payload)
            except WebSocketDisconnect:
                self.disconnect(connection)


manager = ConnectionManager()


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(autopilot_loop())


async def autopilot_loop() -> None:
    while True:
        await asyncio.sleep(1)
        if runtime.state.autopilot and runtime.has_runnable_tasks():
            tick_event = runtime.tick()
            snapshot = runtime.snapshot_state()
            if runtime.state.trace_enabled:
                await manager.broadcast({"type": "trace", "event": tick_event.__dict__})
            await manager.broadcast({"type": "tasks", "tasks": snapshot["tasks"]})
            await manager.broadcast({"type": "docs", "docs": snapshot["docs"]})
            await manager.broadcast({"type": "errors", "errors": snapshot["errors"]})
            await manager.broadcast({"type": "mode", "mode": snapshot["mode"]})


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    return runtime.snapshot_state()


@app.post("/api/chat")
def post_chat(payload: Dict[str, str]) -> Dict[str, Any]:
    text = payload.get("message", "")
    result = runtime.submit_user_goal(text)
    tick_event = runtime.tick()
    snapshot = runtime.snapshot_state()
    logger.info("chat handled: %s", text)
    return {
        "plan": result["plan"],
        "assistant": result["assistant"],
        "tasks": snapshot["tasks"],
        "trace": tick_event.__dict__,
    }


@app.post("/api/tick")
def post_tick() -> Dict[str, Any]:
    tick_event = runtime.tick()
    snapshot = runtime.snapshot_state()
    logger.info("tick executed")
    return {
        "status": "ok",
        "trace": tick_event.__dict__,
        "tasks": snapshot["tasks"],
    }


@app.post("/api/autopilot")
def post_autopilot(payload: Dict[str, Any]) -> Dict[str, Any]:
    on = bool(payload.get("on", False))
    runtime.set_autopilot(on)
    logger.info("autopilot set to %s", on)
    return {"autopilot": on}


@app.post("/api/stop")
def post_stop() -> Dict[str, Any]:
    runtime.stop()
    logger.info("stop invoked")
    return {"status": "stopped"}


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
    await manager.connect(ws)
    await manager.send_json(ws, {"type": "mode", "mode": runtime.state.mode})
    try:
        while True:
            raw = await ws.receive_text()
            payload = json.loads(raw)
            msg_type = payload.get("type")
            if msg_type == "chat":
                text = payload.get("text", "")
                result = runtime.submit_user_goal(text)
                req_id = result["req_id"]
                trace_start = runtime._trace(  # noqa: SLF001
                    phase="chat_start",
                    req_id=req_id,
                    duration_ms=0,
                    tool="ws.chat",
                    args={"text": text},
                    result="received",
                )
                if runtime.state.trace_enabled:
                    await manager.send_json(ws, {"type": "trace", "event": trace_start.__dict__})
                snapshot = runtime.snapshot_state()
                await manager.send_json(ws, {"type": "tasks", "tasks": snapshot["tasks"]})
                await manager.send_json(ws, {"type": "plan", "json": result["plan"]})
                await manager.send_json(ws, {"type": "assistant", "text": result["assistant"]})
                tick_event = runtime.tick()
                if runtime.state.trace_enabled:
                    await manager.send_json(ws, {"type": "trace", "event": tick_event.__dict__})
                snapshot = runtime.snapshot_state()
                await manager.send_json(ws, {"type": "tasks", "tasks": snapshot["tasks"]})
                await manager.send_json(ws, {"type": "mode", "mode": snapshot["mode"]})
                await manager.send_json(ws, {"type": "docs", "docs": snapshot["docs"]})
                await manager.send_json(ws, {"type": "errors", "errors": snapshot["errors"]})
                trace_end = runtime._trace(  # noqa: SLF001
                    phase="chat_end",
                    req_id=req_id,
                    duration_ms=0,
                    tool="ws.chat",
                    result="complete",
                )
                if runtime.state.trace_enabled:
                    await manager.send_json(ws, {"type": "trace", "event": trace_end.__dict__})
            elif msg_type == "tick":
                tick_event = runtime.tick()
                snapshot = runtime.snapshot_state()
                if runtime.state.trace_enabled:
                    await manager.send_json(ws, {"type": "trace", "event": tick_event.__dict__})
                await manager.send_json(ws, {"type": "tasks", "tasks": snapshot["tasks"]})
                await manager.send_json(ws, {"type": "mode", "mode": snapshot["mode"]})
            elif msg_type == "autopilot":
                runtime.set_autopilot(bool(payload.get("on")))
                await manager.send_json(ws, {"type": "mode", "mode": runtime.state.mode})
            elif msg_type == "stop":
                runtime.stop()
                await manager.send_json(ws, {"type": "mode", "mode": runtime.state.mode})
            elif msg_type == "read_file":
                path = payload.get("path", "")
                try:
                    content = runtime.read_workspace_file(path)
                    await manager.send_json(
                        ws, {"type": "file", "path": path, "content": content}
                    )
                except Exception as exc:  # noqa: BLE001
                    runtime.state.errors.append(str(exc))
                    await manager.send_json(ws, {"type": "errors", "errors": runtime.state.errors[-50:]})
    except WebSocketDisconnect:
        manager.disconnect(ws)
        logger.info("websocket disconnected")

