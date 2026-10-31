import { useEffect, useState } from 'react';
import { api } from '../api';
import type { FileInfo } from '../types';
import FileViewer from './FileViewer';
const size = (n: number) =>
  n < 1024
    ? `${n} B`
    : n < 1048576
      ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1048576).toFixed(1)} MB`;
export default function FilesPanel({
  id,
  version,
  path,
  setPath,
}: {
  id: string;
  version: number;
  path: string | null;
  setPath: (path: string | null) => void;
}) {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    api
      .files(id)
      .then((f) => {
        if (alive) {
          setFiles(f);
          setLoading(false);
        }
      })
      .catch(() => {
        if (alive) {
          setFiles([]);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [id, version]);
  if (path) return <FileViewer id={id} path={path} version={version} back={() => setPath(null)} />;
  return (
    <div className="panel-body">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">MADE IN THIS CONVERSATION</div>
          <h2>Shared files</h2>
        </div>
        <span className="count-pill">
          {files.length} {files.length === 1 ? 'file' : 'files'}
        </span>
      </div>
      <p className="panel-intro">Everything the group creates, all in one place.</p>
      {loading ? (
        <div className="file-empty">
          <p>Gathering files…</p>
        </div>
      ) : files.length === 0 ? (
        <div className="file-empty">
          <div className="empty-illustration">
            <div className="paper paper-back" />
            <div className="paper paper-front">
              <span />
              <span />
              <span />
            </div>
            <div className="sparkle">✳</div>
          </div>
          <h3>A home for your ideas</h3>
          <p>Files the group creates will appear here, ready when you need them.</p>
        </div>
      ) : (
        <div className="file-list">
          {files.map((f) => (
            <button className="file-item" key={f.path} onClick={() => setPath(f.path)}>
              <span className="file-icon">▤</span>
              <span className="file-info">
                <strong>{f.path.split('/').pop()}</strong>
                <small>
                  {f.path.includes('/') ? f.path + ' · ' : ''}
                  {size(f.size)}
                </small>
              </span>
              <span className="file-arrow">↗</span>
            </button>
          ))}
        </div>
      )}
      <div className="panel-note">
        <span>✳</span>
        <p>
          <strong>Made for the work ahead.</strong>
          <br />
          The group can create and share files as you chat.
        </p>
      </div>
    </div>
  );
}
