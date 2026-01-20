from __future__ import annotations

from typing import Any, Dict, List


def build_ui_task() -> Dict[str, Any]:
    steps = [
        {
            "type": "tool",
            "tool": "write-file",
            "args": [
                "workspace/ui/api.py",
                "from fastapi import FastAPI\nfrom pydantic import BaseModel\n\nfrom controller import ToolRunner, setup_logger\nfrom interface.llm_local import normalize_user_message, normalized_to_incoming_text, verbalize_response\n\napp = FastAPI()\nrunner = ToolRunner(setup_logger())\n\nclass ChatRequest(BaseModel):\n    message: str\n\n\n@app.post(\"/chat\")\ndef chat(req: ChatRequest) -> dict:\n    req_id = runner.next_request_id()\n    now = runner.now_iso()\n    normalized = normalize_user_message(req.message)\n    llm_incoming = normalized.get(\"raw\", req.message) or req.message\n    llm_incoming_json = normalized_to_incoming_text(normalized)\n    runner.inject_memory_note(req_id, llm_incoming_json)\n    runner.inject_llm_actions(req_id, normalized, now)\n    runner.run_main_step(llm_incoming, req_id, now)\n    tool_requests = runner.collect_tool_requests()\n    runner.handle_tool_requests(tool_requests)\n    runner.run_main_step(llm_incoming, req_id, now)\n    _, json_outputs = runner.get_assistant_outputs()\n    plan_json = json_outputs[-1] if json_outputs else \"\"\n    notes = runner.get_recent_tool_errors()\n    response_text = verbalize_response(req.message, plan_json, notes)\n    if not response_text:\n        response_text = plan_json or \"Unable to generate a response at this time.\"\n    return {\"assistant_says\": response_text, \"assistant_json\": plan_json}\n",
                "Bootstrap UI API",
            ],
            "why": "Bootstrap UI API",
        },
        {
            "type": "tool",
            "tool": "write-file",
            "args": [
                "workspace/ui/web/index.html",
                "<!doctype html>\n<html>\n<head>\n  <meta charset=\"utf-8\" />\n  <title>Yesman Chat</title>\n  <style>\n    body { font-family: Arial, sans-serif; margin: 20px; }\n    #log { white-space: pre-wrap; border: 1px solid #ccc; padding: 10px; height: 300px; overflow: auto; }\n    textarea { width: 100%; }\n  </style>\n</head>\n<body>\n  <h1>Yesman Chat</h1>\n  <div id=\"log\"></div>\n  <textarea id=\"message\" rows=\"4\" placeholder=\"Type a message...\"></textarea>\n  <br />\n  <button id=\"send\">Send</button>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n",
                "Bootstrap UI HTML",
            ],
            "why": "Bootstrap UI HTML",
        },
        {
            "type": "tool",
            "tool": "write-file",
            "args": [
                "workspace/ui/web/app.js",
                "const log = document.getElementById(\"log\");\nconst messageEl = document.getElementById(\"message\");\nconst sendBtn = document.getElementById(\"send\");\n\nfunction appendLine(text) {\n  const line = document.createElement(\"div\");\n  line.textContent = text;\n  log.appendChild(line);\n  log.scrollTop = log.scrollHeight;\n}\n\nasync function sendMessage() {\n  const message = messageEl.value.trim();\n  if (!message) {\n    return;\n  }\n  appendLine(`You: ${message}`);\n  messageEl.value = \"\";\n  try {\n    const response = await fetch(\"/chat\", {\n      method: \"POST\",\n      headers: { \"Content-Type\": \"application/json\" },\n      body: JSON.stringify({ message })\n    });\n    const data = await response.json();\n    appendLine(`Assistant: ${data.assistant_says || \"\"}`);\n    if (data.assistant_json) {\n      appendLine(`JSON: ${data.assistant_json}`);\n    }\n  } catch (error) {\n    appendLine(`Error: ${error}`);\n  }\n}\n\nsendBtn.addEventListener(\"click\", sendMessage);\nmessageEl.addEventListener(\"keydown\", (event) => {\n  if (event.key === \"Enter\" && (event.ctrlKey || event.metaKey)) {\n    sendMessage();\n  }\n});\n",
                "Bootstrap UI JS",
            ],
            "why": "Bootstrap UI JS",
        },
        {
            "type": "tool",
            "tool": "write-file",
            "args": [
                "workspace/ui/requirements.txt",
                "fastapi\nuvicorn\n",
                "Bootstrap UI requirements",
            ],
            "why": "Bootstrap UI requirements",
        },
        {
            "type": "tool",
            "tool": "write-file",
            "args": [
                "workspace/ui/README.md",
                "# Yesman UI\n\n## Setup\n```bash\npython -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt\n```\n\n## Run API\n```bash\nuvicorn api:app --reload --host 0.0.0.0 --port 8000\n```\n\n## Open UI\nOpen `web/index.html` in your browser. The UI expects the API at the same origin; if you need to proxy, use a simple static server that forwards `/chat` to the API.\n",
                "Bootstrap UI README",
            ],
            "why": "Bootstrap UI README",
        },
        {
            "type": "evaluate",
            "kind": "ui_build_check",
            "why": "Verify UI files exist",
        },
    ]
    return {
        "title": "Bootstrap Web UI",
        "goal": "Create workspace/ui FastAPI backend + web frontend + README.",
        "steps": steps,
        "priority": 100,
    }


def research_task(topic: str) -> Dict[str, Any]:
    steps = [
        {
            "type": "tool",
            "tool": "fetch-web",
            "args": [topic, "Gather sources", 5],
            "why": "Fetch sources",
        },
        {"type": "note", "text": "Summarize sources into doc-summary facts."},
        {"type": "evaluate", "kind": "research_summary", "why": "Compile recommendation"},
    ]
    return {
        "title": f"Research: {topic}",
        "goal": f"Research {topic} and provide a recommendation.",
        "steps": steps,
        "priority": 60,
    }


def debug_task(issue: str) -> Dict[str, Any]:
    steps = [
        {"type": "note", "text": "Reproduce and gather info."},
        {"type": "note", "text": "Isolate hypothesis."},
        {"type": "note", "text": "Propose fix steps."},
        {"type": "evaluate", "kind": "debug_check", "why": "Sanity check results"},
    ]
    return {
        "title": f"Debug: {issue}",
        "goal": f"Debug {issue} and propose a fix.",
        "steps": steps,
        "priority": 70,
    }
