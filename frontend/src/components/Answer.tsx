import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, Source } from "../api";

type Props = {
  msg: Message;
  n: number;
  model: string;
  selected: boolean;
  onSelect: () => void;
  onFocus: (chunkId: number | null) => void;
};

// Convierte "[p. 3]" en enlaces internos que se pintan como chips de cita.
const withCitations = (text: string) => text.replace(/\[(?:p|pág|pag)\.?\s*(\d+)\]/gi, "[p. $1](#p-$1)");

export function Question({ msg, n }: { msg: Message; n: number }) {
  return (
    <div className="q">
      <span className="label mono">Pregunta {String(n).padStart(2, "0")}</span>
      <h2>{msg.content}</h2>
    </div>
  );
}

export function Answer({ msg, model, selected, onSelect, onFocus }: Props) {
  const items: Source[] = msg.sources?.items ?? [];
  const byPage = (page: number) => items.find((s) => s.page === page)?.chunk_id ?? null;

  return (
    <div className={`a ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="label mono">
        Respuesta <i>·</i> {model}
        {msg.pending && !msg.content && <span className="thinking">buscando en el documento</span>}
      </span>

      {msg.error ? (
        <div className="err">
          <strong>No se pudo responder.</strong> {msg.error}
        </div>
      ) : (
        <div className={`md ${msg.pending ? "streaming" : ""}`}>
          <Markdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => {
                const m = href?.match(/^#p-(\d+)$/);
                if (!m) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                const id = byPage(Number(m[1]));
                return (
                  <button
                    type="button"
                    className="cite mono"
                    onMouseEnter={() => onFocus(id)}
                    onMouseLeave={() => onFocus(null)}
                    onFocus={() => onFocus(id)}
                    onBlur={() => onFocus(null)}
                  >
                    p{m[1]}
                  </button>
                );
              },
            }}
          >
            {withCitations(msg.content)}
          </Markdown>
        </div>
      )}

      {items.length > 0 && (
        <div className="sources">
          <span className="label mono">Fragmentos consultados</span>
          <div className="src-row">
            {items.map((s, i) => (
              <article
                key={s.chunk_id}
                className="src"
                onMouseEnter={() => onFocus(s.chunk_id)}
                onMouseLeave={() => onFocus(null)}
                tabIndex={0}
                onFocus={() => onFocus(s.chunk_id)}
                onBlur={() => onFocus(null)}
              >
                <header>
                  <b className="serif">{i + 1}</b>
                  <span className="mono">pág {s.page}</span>
                  <span className="score mono">{Math.round(Math.max(0, s.score) * 100)}%</span>
                </header>
                <div className="bar"><i style={{ width: `${Math.max(4, s.score * 100)}%` }} /></div>
                <p>{s.content}</p>
              </article>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
