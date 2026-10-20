import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  fmtTime,
  KIND_TAG,
  type Health,
  type Library,
  type Message,
  type Source,
  type Star,
} from "./api";
import Constellation from "./components/Constellation";
import { Answer, Question } from "./components/Answer";
import Settings from "./components/Settings";

const SUGGESTIONS: Record<string, string[]> = {
  all: [
    "¿Qué contiene la base de conocimiento?",
    "¿Qué muestran las imágenes y gráficos de los PDF?",
    "Resume lo que se dice en los audios y videos",
    "¿Qué cifras aparecen en los gráficos?",
    "¿Qué se ve en las escenas de los videos?",
  ],
  pdf: ["Resume el documento en cinco puntos", "¿De qué trata la página 1?", "¿Qué muestran sus imágenes?"],
  image: ["Describe la imagen", "¿Qué texto aparece en la imagen?", "Explica lo que representa"],
  audio: ["Resume el audio", "¿Cuáles son las ideas principales?", "¿Qué se dice en el minuto 1?"],
  video: ["¿De qué trata el video?", "¿Qué se ve en las escenas?", "¿Qué se dice al principio?"],
};

const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);

function describe(s: Source): string {
  if (s.status === "queued") return "en cola";
  if (s.status === "processing") {
    const p = s.progress;
    return p ? `${p.stage.toLowerCase()}${p.fraction != null ? ` ${Math.round(p.fraction * 100)}%` : "…"}` : "procesando…";
  }
  if (s.status === "error") return s.error ?? "error";
  const m = s.meta;
  const parts =
    s.kind === "pdf" ? [`${m.pages} pp`, m.images ? `${m.images} img` : "", m.scanned_pages ? `${m.scanned_pages} escan.` : ""]
    : s.kind === "image" ? [`${m.width}×${m.height}`]
    : [fmtTime(m.duration ?? 0), m.frames ? `${m.frames} escenas` : "", m.language ?? ""];
  return [...parts.filter(Boolean), `${s.chunk_count} frag`, fmtSize(s.size_bytes)].join(" · ");
}

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [library, setLibrary] = useState<Library | null>(null);
  const [stars, setStars] = useState<Star[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [scope, setScope] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<Message["id"] | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [panel, setPanel] = useState<"chat" | "map">("chat");
  const [railOpen, setRailOpen] = useState(false);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [kick, setKick] = useState(0); // fuerza una consulta inmediata tras subir, borrar o reintentar
  const poll = () => setKick((k) => k + 1);

  const fileInput = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  const limits = health?.limits;
  const model = health?.chat.model ?? "…";
  const ready = sources.filter((s) => s.status === "ready");
  const working = sources.some((s) => s.status === "queued" || s.status === "processing") || !!library?.task?.done;
  const scoped = sources.filter((s) => scope.includes(s.id));
  const accept = limits?.extensions.join(",") ?? ".pdf,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.ogg,.mp4,.mov,.webm,.mkv";

  const refreshHealth = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const refreshSources = useCallback(async () => {
    const res = await api.sources();
    setSources(res.sources);
    setLibrary(res.library);
    setLoaded(true);
    return res;
  }, []);

  useEffect(() => {
    refreshHealth();
    api.messages().then(setMessages).catch(() => {});
  }, [refreshHealth]);

  // La carpeta se vigila en el servidor: se consulta seguido mientras hay trabajo y despacio si no.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let alive = true;
    const tick = async () => {
      let delay = 8000;
      try {
        const { sources, library } = await refreshSources();
        if (sources.some((s) => s.status === "queued" || s.status === "processing") || library.task?.done != null) delay = 1500;
      } catch (e: any) {
        setToast(`No hay conexión con el servidor: ${e.message}`);
      }
      if (alive) timer = setTimeout(tick, delay);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [refreshSources, kick]);

  // El mapa se recalcula en el servidor cuando cambia la base.
  useEffect(() => {
    if (library) api.map().then(setStars).catch(() => {});
  }, [library?.map_version]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fuentes borradas dejan de estar en el filtro.
  useEffect(() => {
    setScope((sc) => sc.filter((id) => sources.some((s) => s.id === id)));
  }, [sources]);

  useEffect(() => {
    if (messages.length) scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  // Fuentes que se dibujan en el mapa: la respuesta elegida o la última.
  const shown = useMemo(() => {
    const answers = messages.filter((m) => m.role === "assistant" && m.sources);
    return answers.find((m) => m.id === selectedId) ?? answers[answers.length - 1] ?? null;
  }, [messages, selectedId]);

  const processing = sources.find((s) => s.status === "processing" && s.progress?.fraction != null);
  const forming = processing ? { total: 36, done: Math.round((processing.progress!.fraction ?? 0) * 36) } : null;

  const upload = async (files: File[]) => {
    if (!files.length || uploading) return;
    const max = (limits?.max_file_mb ?? 200) * 1024 * 1024;
    const big = files.filter((f) => f.size > max);
    const ok = files.filter((f) => f.size <= max);
    if (big.length) setToast(`${big.map((f) => f.name).join(", ")} supera ${limits?.max_file_mb} MB.`);
    if (!ok.length) return;
    setUploading(true);
    setRailOpen(false);
    try {
      const res = await api.upload(ok);
      if (res.rejected.length) setToast(res.rejected.map((r) => `${r.name}: ${r.error}`).join(" "));
      poll();
      setPanel("map");
    } catch (e: any) {
      setToast(e.message);
    } finally {
      setUploading(false);
    }
  };

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
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
    let finished = false;
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      for await (const ev of api.chat(q, scope, ctrl.signal)) {
        if (ev.type === "status") patch((m) => ({ ...m, status: ev.text }));
        else if (ev.type === "sources") patch((m) => ({ ...m, sources: { query: ev.query, items: ev.sources } }));
        else if (ev.type === "token") patch((m) => ({ ...m, content: m.content + ev.text }));
        else if (ev.type === "error") {
          finished = true;
          patch((m) => ({ ...m, pending: false, error: ev.error }));
        } else if (ev.type === "done") {
          finished = true;
          patch((m) => ({ ...m, pending: false, id: ev.message_id }));
          setSelectedId(ev.message_id);
        }
      }
    } catch (e: any) {
      finished = true;
      if (e.name !== "AbortError") patch((m) => ({ ...m, pending: false, error: e.message }));
    } finally {
      if (!finished)
        patch((m) => ({ ...m, error: "La conexión con el servidor se cortó antes de terminar la respuesta." }));
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
    setConfirmDel(null);
    try {
      await api.remove(id);
      poll();
    } catch (e: any) {
      setToast(e.message);
    }
  };

  const toggleScope = (s: Source) => {
    if (s.status !== "ready") return;
    setScope((sc) => (sc.includes(s.id) ? sc.filter((x) => x !== s.id) : [...sc, s.id]));
    setRailOpen(false);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    upload([...(e.dataTransfer.files ?? [])]);
  };

  const suggestions = scoped.length === 1 ? SUGGESTIONS[scoped[0].kind] : SUGGESTIONS.all;
  const problem =
    health && !health.embed.ok
      ? `El servidor de embeddings no responde (${health.embed.model}). Arranca llama-server con EmbeddingGemma`
      : health && !health.chat.ok
        ? health.chat.error?.startsWith("Falta") ? health.chat.error : `El modelo de chat no responde (${health.chat.model})`
        : health && !health.vision.ok
          ? `El modelo de visión no responde (${health.vision.model}): las imágenes no se podrán describir`
          : null;
  let qn = 0;

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
        accept={accept}
        multiple
        hidden
        onChange={(e) => {
          upload([...(e.target.files ?? [])]);
          e.target.value = "";
        }}
      />

      {/* ───────── Fuentes ───────── */}
      <aside className="rail">
        <div className="brand">
          <span className="logo" aria-hidden>
            <i /><i /><i />
          </span>
          <h1>
            astro<em>labio</em>
          </h1>
        </div>

        <button className="drop" onClick={() => fileInput.current?.click()} disabled={uploading}>
          <span className="plus">+</span>
          <span>
            <b>{uploading ? "Subiendo…" : "Añadir a la base"}</b>
            <small className="mono">pdf ≤ {limits?.max_pages ?? 8} págs · imagen · audio · video</small>
          </span>
        </button>

        <span className="kicker mono">
          Fuentes · {String(sources.length).padStart(2, "0")}
          {scope.length > 0 && (
            <button className="linkish" onClick={() => setScope([])}>ver todas</button>
          )}
        </span>
        <nav className="docs">
          {loaded && sources.length === 0 && (
            <p className="hint">Copia archivos en la carpeta de conocimiento o súbelos aquí.</p>
          )}
          {sources.map((s) => (
            <div
              key={s.id}
              className={`doc k-${s.kind} st-${s.status} ${scope.includes(s.id) ? "on" : ""}`}
              onClick={() => toggleScope(s)}
              role="button"
              tabIndex={0}
              title={s.status === "ready" ? "Clic para consultar solo esta fuente" : s.path}
              onKeyDown={(e) => e.key === "Enter" && toggleScope(s)}
            >
              <span className="n mono">{KIND_TAG[s.kind]}</span>
              <span className="meta">
                <b title={s.path}>{s.name.replace(/\.[^.]+$/, "")}</b>
                <small className="mono">{describe(s)}</small>
                {s.status === "processing" && (
                  <span className="progress"><i style={{ width: `${Math.round((s.progress?.fraction ?? 0.05) * 100)}%` }} /></span>
                )}
              </span>
              {s.status === "error" ? (
                <button
                  className="del mono armed"
                  title="Reintentar"
                  onClick={(e) => {
                    e.stopPropagation();
                    api.retry(s.id).then(poll);
                  }}
                >
                  ↻
                </button>
              ) : (
                <button
                  className={`del mono ${confirmDel === s.id ? "armed" : ""}`}
                  title="Borrar de la carpeta y de la base"
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(s.id);
                  }}
                >
                  {confirmDel === s.id ? "¿borrar?" : "✕"}
                </button>
              )}
            </div>
          ))}
        </nav>

        {library && (
          <p className="folder mono" title={library.dir}>
            <i className={working ? "busy" : ""} /> vigilando …/{library.dir.split(/[\\/]/).slice(-1)[0]}
            {library.task?.done != null && ` · ${library.task.stage.toLowerCase()} ${library.task.done}/${library.task.total}`}
          </p>
        )}

        <button className="engine" onClick={() => setSettingsOpen(true)}>
          <span className={`dot ${health ? (problem ? "bad" : "ok") : ""}`} />
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
          <button className="ghost mono only-mobile" onClick={() => setRailOpen(true)}>☰ fuentes</button>
          <div className="title">
            <span className="mono kicker">Consultando</span>
            {scoped.length ? (
              <div className="scope">
                {scoped.map((s) => (
                  <button key={s.id} className="chip mono on" onClick={() => toggleScope(s)} title="Quitar del filtro">
                    {KIND_TAG[s.kind]} {s.name} ✕
                  </button>
                ))}
              </div>
            ) : (
              <b>Toda la base · {ready.length} fuente{ready.length === 1 ? "" : "s"}</b>
            )}
          </div>
          <div className="tools">
            {messages.length > 0 && (
              <button
                className="ghost mono"
                onClick={async () => {
                  await api.clear();
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
          {loaded && sources.length === 0 && (
            <section className="hero">
              <span className="kicker mono">Base de conocimiento multimodal · local</span>
              <h2 className="serif">
                Pregúntale
                <br />
                <em>a tu carpeta.</em>
              </h2>
              <p>
                Reúne PDFs, imágenes, audios y videos cortos en una carpeta. Astrolabio lee el texto, describe las imágenes y
                las escenas, transcribe la voz y lo guarda todo como vectores de EmbeddingGemma en PostgreSQL. Después
                preguntas en lenguaje natural y responde citando la página o el minuto exacto.
              </p>
              <button className="portal" onClick={() => fileInput.current?.click()}>
                <span className="ring" />
                <span className="ring r2" />
                <span className="portal-label">
                  <b className="serif">Suelta archivos</b>
                  <small className="mono">o haz clic para elegir</small>
                </span>
              </button>
              <ol className="steps mono">
                <li><b>01</b> leer</li>
                <li><b>02</b> mirar</li>
                <li><b>03</b> escuchar</li>
                <li><b>04</b> vectorizar</li>
                <li><b>05</b> preguntar</li>
              </ol>
            </section>
          )}

          {sources.length > 0 && messages.length === 0 && (
            <section className="hero small">
              <span className="kicker mono">
                {ready.length} fuentes · {ready.reduce((a, s) => a + s.chunk_count, 0)} fragmentos · {health?.embed.model ?? "embeddings"}
              </span>
              <h2 className="serif">
                ¿Qué quieres <em>saber?</em>
              </h2>
              <div className="suggest">
                {suggestions.map((s) => (
                  <button key={s} onClick={() => send(s)} disabled={!ready.length}>
                    {s} <span className="mono">↗</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <Question key={m.id} msg={m} n={++qn} />
            ) : (
              <Answer
                key={m.id}
                msg={m}
                model={model}
                selected={shown?.id === m.id}
                onSelect={() => setSelectedId(m.id)}
                onFocus={setFocus}
              />
            ),
          )}
        </div>

        {sources.length > 0 && (
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
              placeholder={scoped.length === 1 ? `Pregúntale a «${scoped[0].name}»…` : "Pregúntale a tu base de conocimiento…"}
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
              <button type="submit" className="send" disabled={!draft.trim() || !ready.length} title="Enviar">
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
            {processing ? `${processing.name} · ${processing.progress?.stage.toLowerCase()}` : `${stars.length} fragmentos`}
          </span>
        </header>
        <Constellation
          stars={stars}
          forming={forming}
          query={shown?.sources?.query ?? null}
          hits={shown?.sources?.items ?? []}
          focus={focus}
          scope={scope}
        />
        {!stars.length && !forming && (
          <p className="map-empty">
            Cada punto será un fragmento de tu base, ubicado según su significado: texto, imágenes y voz conviven en el
            mismo espacio. Las preguntas aparecen como un rombo que traza rayos hacia lo que usó para responder.
          </p>
        )}
        <footer className="legend mono">
          <span><i className="lg-star" /> texto</span>
          <span><i className="lg-img" /> imagen · escena</span>
          <span><i className="lg-voice" /> voz</span>
          <span><i className="lg-q" /> pregunta</span>
        </footer>
      </section>

      {dragging && (
        <div className="dropzone">
          <div>
            <h2 className="serif"><em>Suéltalo.</em></h2>
            <p className="mono">PDF · imagen · audio · video</p>
          </div>
        </div>
      )}

      {problem && (
        <button className="banner mono" onClick={() => setSettingsOpen(true)}>
          ⚠ {problem} →
        </button>
      )}

      {toast && <div className="toast mono" role="status">{toast}</div>}

      <Settings
        open={settingsOpen}
        health={health}
        onClose={() => setSettingsOpen(false)}
        onSaved={() => refreshHealth()}
      />
      {railOpen && <div className="scrim open only-mobile" onClick={() => setRailOpen(false)} />}
    </div>
  );
}
