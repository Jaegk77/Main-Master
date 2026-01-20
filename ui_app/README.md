# Yesman Local Console

Local-only terminal-style UI for Yesman. The backend binds to `127.0.0.1` and **does not** accept remote connections or authentication. This is intended for trusted local OS users only.

## Requirements

- Python 3.10+
- Node.js 18+

## Backend

```bash
cd ui_app/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

The backend will create the following directories on startup:

- `workspace/state/`
- `workspace/logs/`

Logs are written to `workspace/logs/yesman.log`.

## Frontend

```bash
cd ui_app/frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Open http://127.0.0.1:5173 in your browser.

## Run both (two terminals)

Terminal 1:

```bash
cd ui_app/backend
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
cd ui_app/frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

## Optional helper script

```bash
./ui_app/run.sh
```

The script starts the backend and frontend in the foreground (two processes). Use `Ctrl+C` to stop both.

## Notes

- The backend only calls the LLM (Ollama) to verbalize MeTTa-generated plans.
- If Ollama is unavailable, the backend emits a fallback response derived from plan JSON and reports the error to the Errors pane.
- All state is persisted under `workspace/state/` and trace events are appended to `workspace/state/events.jsonl`.
