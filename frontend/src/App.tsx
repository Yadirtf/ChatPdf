import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Doc, type Health, type Message, type ProviderCfg, type Star } from "./api";
import Constellation from "./components/Constellation";
import { Answer, Question } from "./components/Answer";
import Settings from "./components/Settings";

type Ingest = { name: string; stage: string; pages?: number; total: number; done: number; error?: string };

const SUGGESTIONS = [
  "Resume el documento en cinco puntos",
  "¿Cuál es la idea principal?",
  "Explícalo como si tuviera doce años",
  "¿Qué datos o cifras menciona?",
];

const STAGES: Record<string, string> = {
  reading: "Leyendo páginas",
  read: "Leyendo páginas",
  chunked: "Fragmentando",
  embedding: "Vectorizando",
  done: "Listo",
};

const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

export default function App() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stars, setStars] = useState<Star[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedId, setSelectedId] = useState<Message["id"] | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [ingest, setIngest] = useState<Ingest | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [cfg, setCfg] = useState<ProviderCfg | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [panel, setPanel] = useState<"chat" | "map">("chat");
  const [railOpen, setRailOpen] = useState(false);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [toast, setToast] = useState("");

  const fileInput = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const prevActive = useRef<string | null>(null);

  const active = docs.find((d) => d.id === activeId) ?? null;
  const limits = health?.limits ?? { max_pages: 8, max_file_mb: 20 };
  const model = cfg ? `${cfg.llm_provider === "ollama" ? "local" : "api"} · ${cfg[cfg.llm_provider].chat_model}` : "…";

  const refreshHealth = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.settings().then(setCfg).catch(() => {});
  }, []);

  useEffect(() => {
    refreshHealth();
    api.documents().then((d) => {
      setDocs(d);
      if (d[0]) setActiveId(d[0].id);
    }).catch((e) => setToast(`No hay conexión con el servidor: ${e.message}`));
  }, [refreshHealth]);

  useEffect(() => {
    abort.current?.abort();
    setSelectedId(null);
    setFocus(null);
    if (!activeId) {
      setStars([]);
      setMessages([]);
      return;
    }
    api.map(activeId).then(setStars).catch(() => setStars([]));
    api.messages(activeId).then(setMessages).catch(() => setMessages([]));
  }, [activeId]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // Fuentes que se dibujan en el mapa: la respuesta elegida o la última.
  const shown = useMemo(() => {
    const answers = messages.filter((m) => m.role === "assistant" && m.sources);
    return answers.find((m) => m.id === selectedId) ?? answers[answers.length - 1] ?? null;
  }, [messages, selectedId]);

  const upload = async (file: File) => {
    if (ingest && !ingest.error && ingest.stage !== "done") return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
      setToast("Solo se aceptan archivos PDF.");
      return;
    }
    if (file.size > limits.max_file_mb * 1024 * 1024) {
      setToast(`El archivo supera ${limits.max_file_mb} MB.`);
      return;
    }
    setRailOpen(false);
    setPanel("map");
    prevActive.current = activeId;
    setActiveId(null);
    setIngest({ name: file.name, stage: "reading", total: 0, done: 0 });
    try {
      for await (const ev of api.upload(file)) {
        if (ev.stage === "error") throw new Error(ev.error);
        setIngest((s) => s && {
          ...s,
          stage: ev.stage,
          pages: ev.pages ?? s.pages,
          total: ev.chunks ?? ev.total ?? s.total,
          done: ev.done ?? s.done,
        });
        if (ev.stage === "done") {
          const doc: Doc = ev.document;
          setDocs((d) => [doc, ...d]);
          setActiveId(doc.id);
          setTimeout(() => setIngest(null), 900);
          setTimeout(() => setPanel("chat"), 2400);
        }
      }
    } catch (e: any) {
      setIngest((s) => s && { ...s, stage: "error", error: e.message });
    }
  };

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || !activeId || busy) return;
    setDraft("");
    setBusy(true);
    const tmp = `tmp-${Date.now()}`;
    setMessages((m) => [
      ...m,
      { id: `${tmp}-q`, role: "user", content: q },
      { id: tmp, role: "assistant", content: "", pending: true },
    ]);
    setSelectedId(tmp);
    const patch = (fn: (m: Message) => Message) => setMessages((all) => all.map((m) => (m.id === tmp ? fn(m) : m)));
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      for await (const ev of api.chat(activeId, q, ctrl.signal)) {
        if (ev.type === "sources") patch((m) => ({ ...m, sources: { query: ev.query, items: ev.sources } }));
        else if (ev.type === "token") patch((m) => ({ ...m, content: m.content + ev.text }));
        else if (ev.type === "error") patch((m) => ({ ...m, pending: false, error: ev.error }));
        else if (ev.type === "done") {
          patch((m) => ({ ...m, pending: false, id: ev.message_id }));
          setSelectedId(ev.message_id);
        }
      }
    } catch (e: any) {
      if (e.name !== "AbortError") patch((m) => ({ ...m, pending: false, error: e.message }));
    } finally {
      patch((m) => ({ ...m, pending: false }));
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (confirmDel !== id) {
      setConfirmDel(id);
      setTimeout(() => setConfirmDel((c) => (c === id ? null : c)), 2500);
      return;
    }
    await api.remove(id);
    setConfirmDel(null);
    setDocs((d) => {
      const rest = d.filter((x) => x.id !== id);
      if (id === activeId) setActiveId(rest[0]?.id ?? null);
      return rest;
    });
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) upload(f);
  };

  let qn = 0;
  const forming = ingest && !ingest.error && ingest.total ? { total: ingest.total, done: ingest.done } : null;

  return (
    <div
      className={`app ${railOpen ? "rail-open" : ""} panel-${panel}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (e.dataTransfer.types.includes("Files")) setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={onDrop}
    >
      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload(f);
          e.target.value = "";
        }}
      />

      {/* ───────── Archivo ───────── */}
      <aside className="rail">
        <div className="brand">
          <span className="logo" aria-hidden>
            <i /><i /><i />
          </span>
          <h1>
            chat<em>pdf</em>
          </h1>
        </div>

        <button className="drop" onClick={() => fileInput.current?.click()}>
          <span className="plus">+</span>
          <span>
            <b>Nuevo PDF</b>
            <small className="mono">
              máx. {limits.max_pages} págs · {limits.max_file_mb} MB
            </small>
          </span>
        </button>

        <span className="kicker mono">Archivo · {String(docs.length).padStart(2, "0")}</span>
        <nav className="docs">
          {docs.length === 0 && <p className="hint">Tus documentos aparecerán aquí.</p>}
          {docs.map((d, i) => (
            <div
              key={d.id}
              className={`doc ${d.id === activeId ? "on" : ""}`}
              onClick={() => {
                setActiveId(d.id);
                setRailOpen(false);
              }}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && setActiveId(d.id)}
            >
              <span className="n mono">{String(docs.length - i).padStart(2, "0")}</span>
              <span className="meta">
                <b title={d.name}>{d.name.replace(/\.pdf$/i, "")}</b>
                <small className="mono">
                  {d.pages} pp · {d.chunk_count} frag · {fmtSize(d.size_bytes)}
                </small>
              </span>
              <button
                className={`del mono ${confirmDel === d.id ? "armed" : ""}`}
                title="Eliminar"
                onClick={(e) => {
                  e.stopPropagation();
                  remove(d.id);
                }}
              >
                {confirmDel === d.id ? "¿borrar?" : "✕"}
              </button>
            </div>
          ))}
        </nav>

        <button className="engine" onClick={() => setSettingsOpen(true)}>
          <span className={`dot ${health?.llm.ok ? "ok" : health ? "bad" : ""}`} />
          <span>
            <small className="mono">motor</small>
            <b className="mono">{model}</b>
          </span>
          <span className="mono arrow">⚙</span>
        </button>
      </aside>

      {/* ───────── Conversación ───────── */}
      <main className="chat">
        <header className="topbar">
          <button className="ghost mono only-mobile" onClick={() => setRailOpen(true)}>☰ archivo</button>
          <div className="title">
            {active ? (
              <>
                <span className="mono kicker">Conversando con</span>
                <b>{active.name}</b>
              </>
            ) : (
              <span className="mono kicker">Sin documento</span>
            )}
          </div>
          <div className="tools">
            {active && messages.length > 0 && (
              <button
                className="ghost mono"
                onClick={async () => {
                  await api.clear(active.id);
                  setMessages([]);
                }}
              >
                limpiar
              </button>
            )}
            <button className="ghost mono only-narrow" onClick={() => setPanel(panel === "map" ? "chat" : "map")}>
              {panel === "map" ? "← chat" : "mapa ✦"}
            </button>
          </div>
        </header>

        <div className="scroll" ref={scroller}>
          {!active && !ingest && (
            <section className="hero">
              <span className="kicker mono">Observatorio de documentos · RAG local</span>
              <h2 className="serif">
                Haz que tus PDFs
                <br />
                <em>respondan.</em>
              </h2>
              <p>
                Sube un PDF de hasta {limits.max_pages} páginas. Lo partimos en fragmentos, los convertimos en vectores
                dentro de PostgreSQL y cada pregunta viaja hasta los pasajes más cercanos. Puedes verlo ocurrir en el mapa.
              </p>
              <button className="portal" onClick={() => fileInput.current?.click()}>
                <span className="ring" />
                <span className="ring r2" />
                <span className="portal-label">
                  <b className="serif">Suelta un PDF</b>
                  <small className="mono">o haz clic para elegir</small>
                </span>
              </button>
              <ol className="steps mono">
                <li><b>01</b> leer</li>
                <li><b>02</b> fragmentar</li>
                <li><b>03</b> vectorizar</li>
                <li><b>04</b> conversar</li>
              </ol>
            </section>
          )}

          {!active && ingest && (
            <section className="hero ingesting">
              <span className="kicker mono">{ingest.error ? "Algo salió mal" : "Indexando"}</span>
              <h2 className="serif">
                <em>{ingest.name.replace(/\.pdf$/i, "")}</em>
              </h2>
              {ingest.error ? (
                <>
                  <p className="err">{ingest.error}</p>
                  <button className="primary" onClick={() => { setIngest(null); setPanel("chat"); setActiveId(prevActive.current); }}>
                    Entendido
                  </button>
                </>
              ) : (
                <ol className="pipeline mono">
                  {["read", "chunked", "embedding", "done"].map((s, i) => {
                    const order = ["reading", "read", "chunked", "embedding", "done"];
                    const cur = order.indexOf(ingest.stage);
                    const me = order.indexOf(s);
                    const state = cur > me || ingest.stage === "done" ? "done" : cur >= me - 1 ? "now" : "";
                    const detail =
                      s === "read" ? (ingest.pages ? `${ingest.pages} págs` : "")
                      : s === "chunked" ? (ingest.total ? `${ingest.total} fragmentos` : "")
                      : s === "embedding" ? (ingest.total ? `${ingest.done}/${ingest.total}` : "")
                      : "";
                    return (
                      <li key={s} className={state}>
                        <b>{String(i + 1).padStart(2, "0")}</b> {STAGES[s]} <i>{detail}</i>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          )}

          {active && messages.length === 0 && (
            <section className="hero small">
              <span className="kicker mono">
                {active.pages} páginas · {active.chunk_count} fragmentos · {active.embed_model}
              </span>
              <h2 className="serif">
                ¿Qué quieres <em>saber?</em>
              </h2>
              <div className="suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)}>
                    {s} <span className="mono">↗</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {active &&
            messages.map((m) =>
              m.role === "user" ? (
                <Question key={m.id} msg={m} n={++qn} />
              ) : (
                <Answer
                  key={m.id}
                  msg={m}
                  n={qn}
                  model={model}
                  selected={shown?.id === m.id}
                  onSelect={() => setSelectedId(m.id)}
                  onFocus={setFocus}
                />
              ),
            )}
        </div>

        {active && (
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
          >
            <textarea
              value={draft}
              rows={1}
              placeholder="Pregúntale al documento…"
              onChange={(e) => {
                setDraft(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(draft);
                }
              }}
            />
            {busy ? (
              <button type="button" className="send stop" onClick={() => abort.current?.abort()} title="Detener">
                ■
              </button>
            ) : (
              <button type="submit" className="send" disabled={!draft.trim()} title="Enviar">
                ↵
              </button>
            )}
          </form>
        )}
      </main>

      {/* ───────── Mapa semántico ───────── */}
      <section className="map">
        <header>
          <span className="kicker mono">Mapa semántico</span>
          <span className="mono count">
            {forming ? `${forming.done}/${forming.total}` : `${stars.length} fragmentos`}
          </span>
        </header>
        <Constellation
          stars={stars}
          forming={forming}
          query={shown?.sources?.query ?? null}
          hits={shown?.sources?.items ?? []}
          focus={focus}
        />
        {!stars.length && !forming && (
          <p className="map-empty">
            Cada punto será un fragmento de tu PDF, ubicado según su significado. Las preguntas aparecen como un rombo
            que traza rayos hacia los pasajes que usó para responder.
          </p>
        )}
        <footer className="legend mono">
          <span><i className="lg-star" /> fragmento</span>
          <span><i className="lg-q" /> pregunta</span>
          <span><i className="lg-line" /> hilo de lectura</span>
        </footer>
      </section>

      {dragging && (
        <div className="dropzone">
          <div>
            <h2 className="serif"><em>Suéltalo.</em></h2>
            <p className="mono">PDF · máx. {limits.max_pages} páginas</p>
          </div>
        </div>
      )}

      {health && !health.llm.ok && (
        <button className="banner mono" onClick={() => setSettingsOpen(true)}>
          ⚠ El motor no responde ({health.llm.provider}). Revisa que Ollama esté activo o configura una API key →
        </button>
      )}

      {toast && <div className="toast mono" role="status">{toast}</div>}

      <Settings
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={(c) => {
          setCfg(c);
          api.health().then(setHealth).catch(() => {});
        }}
      />
      {railOpen && <div className="scrim open only-mobile" onClick={() => setRailOpen(false)} />}
    </div>
  );
}
