import { useCallback, useEffect, useMemo, useState } from 'react';
import TerminalPane from './components/TerminalPane';
import ActivityPane from './components/ActivityPane';
import TaskPane from './components/TaskPane';
import NavigatorPane from './components/NavigatorPane';
import LogsOverlay from './components/Overlays/LogsOverlay';
import DocsOverlay from './components/Overlays/DocsOverlay';
import FilesOverlay from './components/Overlays/FilesOverlay';
import SettingsOverlay from './components/Overlays/SettingsOverlay';
import ErrorsOverlay from './components/Overlays/ErrorsOverlay';
import CandidatesOverlay from './components/Overlays/CandidatesOverlay';
import TaskDetailsOverlay from './components/Overlays/TaskDetailsOverlay';

interface TaskItem {
  id: string;
  title: string;
  status: string;
  progress?: string;
  details?: string;
}

interface TraceEvent {
  ts: string;
  phase: string;
  duration_ms: number;
  tool?: string;
  result?: string;
  error?: string;
}

interface OutputLine {
  id: string;
  text: string;
}

const WS_URL = 'ws://127.0.0.1:8000/ws';
const HTTP_URL = 'http://127.0.0.1:8000';

function useWebSocket(onStatusChange: (connected: boolean) => void) {
  const [socket, setSocket] = useState<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    setSocket(ws);
    ws.onopen = () => onStatusChange(true);
    ws.onclose = () => onStatusChange(false);
    ws.onerror = () => onStatusChange(false);
    return () => {
      ws.close();
    };
  }, [onStatusChange]);

  return socket;
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const socket = useWebSocket(setConnected);
  const [output, setOutput] = useState<OutputLine[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [docs, setDocs] = useState<Array<{ title?: string; url?: string }>>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [mode, setMode] = useState('idle');
  const [autopilot, setAutopilot] = useState(false);
  const [activeOverlay, setActiveOverlay] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [fileContent, setFileContent] = useState('');
  const [selectedTask, setSelectedTask] = useState<TaskItem | null>(null);
  const budgets = useMemo(
    () => ({
      max_tool_calls_per_tick: 5,
      max_web_fetches_per_tick: 2,
      max_llm_calls_per_tick: 1
    }),
    []
  );

  const appendOutput = useCallback((text: string) => {
    setOutput((prev) => [...prev, { id: crypto.randomUUID(), text }]);
  }, []);

  useEffect(() => {
    if (!socket) {
      return;
    }
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'assistant') {
        appendOutput(`assistant: ${data.text}`);
      }
      if (data.type === 'plan') {
        appendOutput(`plan: ${JSON.stringify(data.json)}`);
      }
      if (data.type === 'trace') {
        setEvents((prev) => [...prev.slice(-200), data.event]);
      }
      if (data.type === 'tasks') {
        setTasks(data.tasks ?? []);
      }
      if (data.type === 'docs') {
        setDocs(data.docs ?? []);
      }
      if (data.type === 'errors') {
        setErrors(data.errors ?? []);
      }
      if (data.type === 'file') {
        setFileContent(data.content ?? '');
      }
      if (data.type === 'mode') {
        setMode(data.mode ?? 'idle');
      }
    };
  }, [appendOutput, socket]);

  useEffect(() => {
    if (!connected) {
      appendOutput('system: backend disconnected.');
      setErrors((prev) => ['Backend disconnected.', ...prev]);
      return;
    }
    appendOutput('system: backend connected.');
    fetch(`${HTTP_URL}/api/state`)
      .then((response) => response.json())
      .then((data) => {
        setTasks(data.tasks ?? []);
        setEvents(data.events ?? []);
        setDocs(data.docs ?? []);
        setErrors(data.errors ?? []);
        setMode(data.mode ?? 'idle');
        setAutopilot(Boolean(data.autopilot));
      })
      .catch(() => {
        setErrors((prev) => ['Failed to load initial state.', ...prev]);
      });
  }, [appendOutput, connected]);

  const sendMessage = useCallback(
    (text: string) => {
      if (text.trim() === '/help') {
        appendOutput('system: /help, /plan, /status');
        return;
      }
      if (text.trim() === '/status') {
        appendOutput(`system: mode=${mode} autopilot=${autopilot}`);
        return;
      }
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        appendOutput('system: backend offline, unable to send.');
        setErrors((prev) => ['Backend offline.', ...prev]);
        return;
      }
      if (text.trim() === '/plan') {
        appendOutput('system: plan entries appear after your last message.');
        return;
      }
      socket.send(JSON.stringify({ type: 'chat', text }));
      appendOutput(`you: ${text}`);
    },
    [appendOutput, autopilot, mode, socket]
  );

  const handleTick = useCallback(() => {
    socket?.send(JSON.stringify({ type: 'tick' }));
  }, [socket]);

  const handleAutopilot = useCallback(
    (on: boolean) => {
      socket?.send(JSON.stringify({ type: 'autopilot', on }));
      setAutopilot(on);
    },
    [socket]
  );

  const handleStop = useCallback(() => {
    socket?.send(JSON.stringify({ type: 'stop' }));
    setAutopilot(false);
  }, [socket]);

  const toggleOverlay = useCallback(
    (name: string) => {
      setActiveOverlay((prev) => (prev === name ? null : name));
    },
    []
  );

  const refreshLogs = useCallback(async () => {
    const response = await fetch(`${HTTP_URL}/api/logs/tail?n=200`);
    const data = await response.json();
    setLogs(data.lines ?? []);
  }, []);

  const refreshFiles = useCallback(async () => {
    const response = await fetch(`${HTTP_URL}/api/workspace/list`);
    const data = await response.json();
    setFiles(data.files ?? []);
  }, []);

  const openFile = useCallback(async (path: string) => {
    const response = await fetch(
      `${HTTP_URL}/api/workspace/open?path=${encodeURIComponent(path)}`
    );
    const data = await response.json();
    setFileContent(data.content ?? '');
  }, []);

  useEffect(() => {
    if (activeOverlay === 'Logs') {
      refreshLogs();
    }
    if (activeOverlay === 'Files') {
      refreshFiles();
    }
  }, [activeOverlay, refreshFiles, refreshLogs]);

  return (
    <div className="app">
      <div className="pane">
        <h2>Terminal</h2>
        <TerminalPane onSend={sendMessage} output={output} />
      </div>
      <ActivityPane events={events} />
      <TaskPane tasks={tasks} onSelect={setSelectedTask} />
      <NavigatorPane
        active={activeOverlay}
        onToggle={toggleOverlay}
        autopilot={autopilot}
        mode={mode}
        connected={connected}
        onTick={handleTick}
        onAutopilot={handleAutopilot}
        onStop={handleStop}
      />
      {activeOverlay === 'Logs' ? (
        <LogsOverlay lines={logs} onClose={() => setActiveOverlay(null)} onRefresh={refreshLogs} />
      ) : null}
      {activeOverlay === 'Docs' ? (
        <DocsOverlay docs={docs} onClose={() => setActiveOverlay(null)} />
      ) : null}
      {activeOverlay === 'Files' ? (
        <FilesOverlay
          files={files}
          content={fileContent}
          onClose={() => setActiveOverlay(null)}
          onOpen={openFile}
        />
      ) : null}
      {activeOverlay === 'Settings' ? (
        <SettingsOverlay
          budgets={budgets}
          model="llama3"
          autopilot={autopilot}
          onAutopilot={handleAutopilot}
          onClose={() => setActiveOverlay(null)}
        />
      ) : null}
      {activeOverlay === 'Errors' ? (
        <ErrorsOverlay errors={errors} onClose={() => setActiveOverlay(null)} />
      ) : null}
      {activeOverlay === 'Candidates' ? (
        <CandidatesOverlay onClose={() => setActiveOverlay(null)} />
      ) : null}
      {selectedTask ? (
        <TaskDetailsOverlay task={selectedTask} onClose={() => setSelectedTask(null)} />
      ) : null}
    </div>
  );
}
