import DOMPurify from 'dompurify';
import { marked } from 'marked';
import type { RosterAgent } from '../avatarBar';
import type { DisplayMessage } from '../state';

export function Markdown({ text }: { text: string }) {
  const html = DOMPurify.sanitize(marked.parse(text, { async: false, breaks: true }));
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}
export function Avatar({
  agent,
  size = '',
}: {
  agent: { name: string; color: string };
  size?: string;
}) {
  return (
    <div className={`avatar ${size}`} style={{ backgroundColor: agent.color }} aria-hidden="true">
      {agent.name.charAt(0)}
    </div>
  );
}
function ToolLine({ summary, openFile }: { summary: string; openFile: (path: string) => void }) {
  const parts = summary.split(/([\w./-]+\.(?:md|csv|html|txt|json|py|png|jpe?g|svg|pdf))/gi);
  return (
    <div className="tool-line">
      <span className="tool-symbol">↳</span>{' '}
      {parts.map((part, i) =>
        /[\w/-]+\.(?:md|csv|html|txt|json|py|png|jpe?g|svg|pdf)$/i.test(part) ? (
          <button key={i} onClick={() => openFile(part)}>
            {part}
          </button>
        ) : (
          part
        ),
      )}
    </div>
  );
}
export default function MessageBubble({
  message,
  agents,
  openFile,
}: {
  message: DisplayMessage;
  agents: RosterAgent[];
  openFile: (path: string) => void;
}) {
  const user = message.author === 'user';
  const agent = agents.find((a) => a.key === message.author);
  const replies = (message.reply_to ?? [])
    .map((key) => agents.find((a) => a.key === key)?.name ?? key)
    .join(', ');
  if (user)
    return (
      <div className="message-row user-row">
        <div className="user-bubble">{message.text}</div>
      </div>
    );
  return (
    <div className="message-row agent-row">
      {agent && agent.known ? (
        <Avatar agent={agent} />
      ) : (
        <div className="avatar fallback" data-key={message.author} title={message.author}>
          {message.author.charAt(0).toUpperCase()}
        </div>
      )}
      <div className="agent-message">
        <div className="message-meta">
          <strong style={{ color: agent?.color }}>{agent?.name ?? message.author}</strong>
          <span>{agent?.role}</span>
        </div>
        <div className="agent-bubble">
          {message.tools.map((tool, i) => (
            <ToolLine key={i} summary={tool.summary} openFile={openFile} />
          ))}
          {replies && <div className="reply-to">→ {replies}</div>}
          <div className="message-text">
            <Markdown text={message.text} />
            {message.status === 'streaming' &&
              (message.text.length === 0 && message.tools.length === 0 ? (
                <span className="typing-dots" aria-label="Writing">
                  <i />
                  <i />
                  <i />
                </span>
              ) : (
                <span className="caret" aria-label="Writing" />
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}
