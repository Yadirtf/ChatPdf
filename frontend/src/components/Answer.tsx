import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { assetUrl, KIND_TAG, locator, MODALITY_LABEL, openAt, type Hit, type Message } from "../api";

type Props = {
  msg: Message;
  model: string;
  selected: boolean;
  onSelect: () => void;
  onFocus: (chunkId: number | null) => void;
};

// Convierte "[3]" en enlaces internos que se pintan como chips de cita.
const withCitations = (text: string) => text.replace(/\[(\d{1,2})\](?!\()/g, "[$1](#cite-$1)");

// Segundos transcurridos: con modelos locales en CPU la primera palabra puede tardar.
function Elapsed() {
  const [s, setS] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setS((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return <i className="elapsed">{s}s</i>;
}

export function Question({ msg, n }: { msg: Message; n: number }) {
  return (
    <div className="q">
      <span className="label mono">Pregunta {String(n).padStart(2, "0")}</span>
      <h2>{msg.content}</h2>
    </div>
  );
}

function SourceCard({ hit, onFocus }: { hit: Hit; onFocus: (id: number | null) => void }) {
  const where = locator(hit);
  return (
    <article
      className={`src m-${hit.modality}`}
      id={`hit-${hit.chunk_id}`}
      onMouseEnter={() => onFocus(hit.chunk_id)}
      onMouseLeave={() => onFocus(null)}
      tabIndex={0}
      onFocus={() => onFocus(hit.chunk_id)}
      onBlur={() => onFocus(null)}
    >
      <header>
        <b className="serif">{hit.n}</b>
        <span className="mono tag">{MODALITY_LABEL[hit.modality]}</span>
        <span className="score mono">{Math.round(Math.max(0, hit.score) * 100)}%</span>
      </header>
      <div className="bar"><i style={{ width: `${Math.max(4, hit.score * 100)}%` }} /></div>
      {hit.asset_id && <img src={assetUrl(hit.asset_id)} alt="" loading="lazy" />}
      <p>{hit.content}</p>
      <a className="origin mono" href={openAt(hit)} target="_blank" rel="noreferrer" title="Abrir el original en este punto">
        <span>{KIND_TAG[hit.kind]}</span> {hit.name}
        {where && <em> · {where}</em>} ↗
      </a>
    </article>
  );
}

export function Answer({ msg, model, selected, onSelect, onFocus }: Props) {
  const items: Hit[] = msg.sources?.items ?? [];
  const byN = new Map(items.map((h) => [h.n, h]));

  return (
    <div className={`a ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="label mono">
        Respuesta <i>·</i> {msg.sources?.model ?? model}
        {msg.pending && !msg.content && (
          <span className="thinking">
            {(msg.status || "Buscando en la base").toLowerCase()} <Elapsed />
          </span>
        )}
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
                const m = href?.match(/^#cite-(\d+)$/);
                if (!m) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                const hit = byN.get(Number(m[1]));
                const id = hit?.chunk_id ?? null;
                return (
                  <button
                    type="button"
                    className={`cite mono ${hit ? `m-${hit.modality}` : ""}`}
                    title={hit ? `${hit.name}${locator(hit) ? ` · ${locator(hit)}` : ""}` : undefined}
                    onMouseEnter={() => onFocus(id)}
                    onMouseLeave={() => onFocus(null)}
                    onFocus={() => onFocus(id)}
                    onBlur={() => onFocus(null)}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (id) document.getElementById(`hit-${id}`)?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
                    }}
                  >
                    {m[1]}
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
            {items.map((h) => <SourceCard key={h.chunk_id} hit={h} onFocus={onFocus} />)}
          </div>
        </div>
      )}
    </div>
  );
}
