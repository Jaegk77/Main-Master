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
}

const WS_URL = 'ws://127.0.0.1:8000/ws';

function useWebSocket() {
  const [socket, setSocket] = useState<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    setSocket(ws);
    return () => {
      ws.close();
    };
  }, []);

  return socket;
}

export default function App() {
  const socket = useWebSocket();
  const [output, setOutput] = useState<string[]>([]);
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
  const budgets = useMemo(
    () => ({
      max_tool_calls_per_tick: 5,
      max_web_fetches_per_tick: 2,
      max_llm_calls_per_tick: 1
    }),
    []
  );

  useEffect(() => {
    if (!socket) {
      return;
    }
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'assistant') {
        setOutput((prev) => [...prev, `assistant: ${data.text}`]);
      }
      if (data.type === 'plan') {
        setOutput((prev) => [...prev, `plan: ${JSON.stringify(data.json)}`]);
      }
      if (data.type === 'trace') {
        setEvents((prev) => [...prev.slice(-200), data.event]);
      }
      if (data.type === 'tasks') {
        setTasks(data.tasks ?? []);
        setMode(data.mode ?? 'idle');
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
    };
  }, [socket]);

  const sendMessage = useCallback(
    (text: string) => {
      if (!socket) {
        return;
      }
      if (text.trim() === '/plan') {
        setOutput((prev) => [...prev, 'Plan already streamed above.']);
        return;
      }
      socket.send(JSON.stringify({ type: 'chat', text }));
      setOutput((prev) => [...prev, `you: ${text}`]);
    },
    [socket]
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

  const toggleOverlay = useCallback(
    (name: string) => {
      setActiveOverlay((prev) => (prev === name ? null : name));
    },
    []
  );

  const refreshLogs = useCallback(async () => {
    const response = await fetch('http://127.0.0.1:8000/api/logs/tail?n=200');
    const data = await response.json();
    setLogs(data.lines ?? []);
  }, []);

  const refreshFiles = useCallback(async () => {
    const response = await fetch('http://127.0.0.1:8000/api/workspace/list');
    const data = await response.json();
    setFiles(data.files ?? []);
  }, []);

  const openFile = useCallback(
    async (path: string) => {
      const response = await fetch(
        `http://127.0.0.1:8000/api/workspace/open?path=${encodeURIComponent(path)}`
      );
      const data = await response.json();
      setFileContent(data.content ?? '');
    },
    []
  );

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
      <TaskPane tasks={tasks} />
      <NavigatorPane
        active={activeOverlay}
        onToggle={toggleOverlay}
        autopilot={autopilot}
        mode={mode}
        onTick={handleTick}
        onAutopilot={handleAutopilot}
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
        <SettingsOverlay budgets={budgets} model="llama3" onClose={() => setActiveOverlay(null)} />
      ) : null}
      {activeOverlay === 'Errors' ? (
        <ErrorsOverlay errors={errors} onClose={() => setActiveOverlay(null)} />
      ) : null}
      {activeOverlay === 'Candidates' ? (
        <CandidatesOverlay onClose={() => setActiveOverlay(null)} />
      ) : null}
    </div>
  );
}
