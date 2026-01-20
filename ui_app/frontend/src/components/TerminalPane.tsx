import { useEffect, useRef } from 'react';
import { Terminal } from 'xterm';
import 'xterm/css/xterm.css';

interface TerminalPaneProps {
  onSend: (text: string) => void;
  output: string[];
}

const PROMPT = '> ';

export default function TerminalPane({ onSend, output }: TerminalPaneProps) {
  const terminalRef = useRef<Terminal | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const bufferRef = useRef('');

  useEffect(() => {
    const term = new Terminal({
      fontFamily: 'Fira Mono, monospace',
      fontSize: 13,
      theme: {
        background: '#0b0f14',
        foreground: '#d9e1e8'
      }
    });
    terminalRef.current = term;
    if (containerRef.current) {
      term.open(containerRef.current);
      term.writeln('Yesman Console ready. Type your request and press Enter.');
      term.write(PROMPT);
    }

    term.onData((data) => {
      if (data === '\r') {
        const value = bufferRef.current.trim();
        term.write('\r\n');
        if (value) {
          onSend(value);
        }
        bufferRef.current = '';
        term.write(PROMPT);
      } else if (data === '\u007f') {
        if (bufferRef.current.length > 0) {
          bufferRef.current = bufferRef.current.slice(0, -1);
          term.write('\b \b');
        }
      } else if (data === '\u000c') {
        term.clear();
      } else {
        bufferRef.current += data;
        term.write(data);
      }
    });

    return () => {
      term.dispose();
    };
  }, [onSend]);

  useEffect(() => {
    const term = terminalRef.current;
    if (!term) {
      return;
    }
    output.forEach((line) => {
      term.writeln(`\r\n${line}`);
      term.write(PROMPT + bufferRef.current);
    });
  }, [output]);

  return <div className="terminal-container" ref={containerRef} />;
}
