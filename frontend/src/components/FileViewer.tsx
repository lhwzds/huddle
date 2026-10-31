import { useEffect, useState } from 'react';
import { api } from '../api';
import type { FileContent } from '../types';
import { Markdown } from './MessageBubble';
function csvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [],
    cell = '',
    quoted = false;
  for (let i = 0; i < text.length && rows.length < 200; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === ',' && !quoted) {
      row.push(cell);
      cell = '';
    } else if ((c === '\n' || c === '\r') && !quoted) {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
      if (c === '\r' && text[i + 1] === '\n') i++;
    } else cell += c;
  }
  if (rows.length < 200 && (cell || row.length)) rows.push([...row, cell]);
  return rows;
}
export default function FileViewer({
  id,
  path,
  version,
  back,
}: {
  id: string;
  path: string;
  version: number;
  back: () => void;
}) {
  const [file, setFile] = useState<FileContent | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    setError(false);
    setFile(null);
    api
      .file(id, path)
      .then((f) => {
        if (alive) setFile(f);
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [id, path, version]);
  const ext = path.split('.').pop()?.toLowerCase();
  const mime =
    ext === 'svg' ? 'image/svg+xml' : ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png';
  const rows = file && ext === 'csv' ? csvRows(file.content) : [];
  return (
    <div className="viewer">
      <button className="back-button" onClick={back}>
        ← <span>All files</span>
      </button>
      <div className="viewer-heading">
        <div className="file-icon">▤</div>
        <div>
          <h2>{path.split('/').pop()}</h2>
          <span>{path}</span>
        </div>
      </div>
      <div className="viewer-content">
        {error ? (
          <p>Couldn't open this file.</p>
        ) : !file ? (
          <p>Opening file…</p>
        ) : ext === 'md' ? (
          <Markdown text={file.content} />
        ) : ext === 'csv' ? (
          <div className="table-wrap">
            <table>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) =>
                      i === 0 ? <th key={j}>{cell}</th> : <td key={j}>{cell}</td>,
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : ext === 'html' ? (
          <iframe title={path} sandbox="" srcDoc={file.content} />
        ) : file.binary && ['png', 'jpg', 'jpeg', 'svg'].includes(ext ?? '') ? (
          <img alt={path} src={`data:${mime};base64,${file.content}`} />
        ) : ext === 'svg' ? (
          // A text SVG is shown as an image too; inside <img> its scripts never run.
          <img alt={path} src={`data:${mime};charset=utf-8,${encodeURIComponent(file.content)}`} />
        ) : (
          <pre>{file.content}</pre>
        )}
      </div>
    </div>
  );
}
