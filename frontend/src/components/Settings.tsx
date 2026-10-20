import { useEffect, useState } from "react";
import { api, type Health, type Provider, type ProviderCfg } from "../api";

type Props = { open: boolean; health: Health | null; onClose: () => void; onSaved: (cfg: ProviderCfg) => void };
type Section = Provider | "embed";

const PROVIDERS: { id: Provider; name: string; note: string }[] = [
  { id: "llamacpp", name: "llama.cpp", note: "local" },
  { id: "ollama", name: "Ollama", note: "local" },
  { id: "cloud", name: "Nube", note: "API key" },
];

// Modelos de chat con visión cuando el proveedor la ofrece: sirven para responder y para describir imágenes.
const PRESETS = [
  { name: "OpenAI", url: "https://api.openai.com/v1", chat: "gpt-4o-mini" },
  { name: "Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai", chat: "gemini-2.5-flash" },
  { name: "OpenRouter", url: "https://openrouter.ai/api/v1", chat: "google/gemma-3-27b-it:free" },
  { name: "Groq", url: "https://api.groq.com/openai/v1", chat: "meta-llama/llama-4-scout-17b-16e-instruct" },
  { name: "Mistral", url: "https://api.mistral.ai/v1", chat: "mistral-small-latest" },
];

export default function Settings({ open, health, onClose, onSaved }: Props) {
  const [cfg, setCfg] = useState<ProviderCfg | null>(null);
  const [models, setModels] = useState<Record<Section, string[]>>({ llamacpp: [], ollama: [], cloud: [], embed: [] });
  const [probe, setProbe] = useState<Partial<Record<Section, string>>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!open) return;
    setMsg("");
    api.settings().then(setCfg).catch((e) => setMsg(e.message));
  }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const loadModels = async (s: Section) => {
    setProbe((p) => ({ ...p, [s]: "probando…" }));
    try {
      const { models } = await api.models(s);
      setModels((m) => ({ ...m, [s]: models }));
      setProbe((p) => ({ ...p, [s]: `conectado · ${models.length} modelo${models.length === 1 ? "" : "s"}` }));
    } catch (e: any) {
      setProbe((p) => ({ ...p, [s]: e.message }));
    }
  };

  if (!cfg) return <aside className={`sheet ${open ? "open" : ""}`} aria-hidden={!open} />;

  const set = (s: Section, k: string, v: string) => setCfg({ ...cfg, [s]: { ...cfg[s], [k]: v } });
  const used = (p: Provider) => cfg.chat_provider === p || cfg.vision_provider === p;

  const save = async (andProbe?: Section) => {
    setSaving(true);
    try {
      const saved = await api.saveSettings(cfg);
      setCfg(saved);
      onSaved(saved);
      setMsg("Guardado.");
      if (andProbe) await loadModels(andProbe);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setSaving(false);
    }
  };

  const field = (s: Section, k: string, label: string, opts: { type?: string; list?: boolean; ph?: string } = {}) => (
    <label className="field">
      <span className="mono">{label}</span>
      <input
        type={opts.type ?? "text"}
        value={(cfg[s] as any)[k] ?? ""}
        placeholder={opts.ph}
        list={opts.list ? `models-${s}` : undefined}
        onChange={(e) => set(s, k, e.target.value)}
        onFocus={(e) => k === "api_key" && e.target.value.startsWith("•") && set(s, k, "")}
        spellCheck={false}
        autoComplete="off"
      />
    </label>
  );

  const picker = (key: "chat_provider" | "vision_provider") => (
    <div className="seg three">
      {PROVIDERS.map((p) => (
        <button key={p.id} className={cfg[key] === p.id ? "on" : ""} onClick={() => setCfg({ ...cfg, [key]: p.id })}>
          <b>{p.name}</b>
          <small className="mono">{p.note}</small>
        </button>
      ))}
    </div>
  );

  return (
    <>
      <div className={`scrim ${open ? "open" : ""}`} onClick={onClose} />
      <aside className={`sheet ${open ? "open" : ""}`} aria-hidden={!open} role="dialog" aria-label="Configuración">
        <header>
          <h3 className="serif">Motor</h3>
          <button className="ghost mono" onClick={onClose}>cerrar ✕</button>
        </header>

        <section>
          <span className="kicker mono">01 — Quién responde</span>
          {picker("chat_provider")}
        </section>

        <section>
          <span className="kicker mono">02 — Quién mira las imágenes</span>
          {picker("vision_provider")}
          <p className="hint">
            Describe las imágenes de los PDF, las imágenes sueltas y las escenas de los videos al indexarlos. Necesita un
            modelo multimodal (gemma-3, gemma4, gpt-4o-mini, gemini…). Si el de chat también lo es, además mira las
            imágenes recuperadas al responder.
          </p>
        </section>

        <section>
          <span className="kicker mono">03 — Embeddings · una sola base vectorial</span>
          {field("embed", "base_url", "URL (llama-server --embeddings)")}
          {field("embed", "model", "Modelo", { list: true, ph: "embeddinggemma-300m" })}
          <div className="row">
            <button className="ghost mono" onClick={() => save("embed")}>guardar y probar →</button>
            <button
              className="ghost mono"
              onClick={() => api.reembed().then(() => setMsg("Revectorizando la base en segundo plano…"))}
            >
              revectorizar todo ↻
            </button>
          </div>
          {probe.embed && <p className="hint mono">{probe.embed}</p>}
          <p className="hint">
            EmbeddingGemma ({health?.embed.dim ?? 768} dimensiones) servido por llama.cpp. Cambiar de modelo revectoriza
            la base sola: el texto, las transcripciones y las descripciones de imágenes se conservan.
          </p>
        </section>

        <section className={used("llamacpp") ? "" : "dim"}>
          <span className="kicker mono">llama.cpp</span>
          {field("llamacpp", "base_url", "URL (llama-server)")}
          {field("llamacpp", "chat_model", "Modelo", { list: true, ph: "gemma-3-4b-it" })}
          <button className="ghost mono" onClick={() => save("llamacpp")}>guardar y probar →</button>
          {probe.llamacpp && <p className="hint mono">{probe.llamacpp}</p>}
        </section>

        <section className={used("ollama") ? "" : "dim"}>
          <span className="kicker mono">Ollama</span>
          {field("ollama", "base_url", "URL")}
          {field("ollama", "chat_model", "Modelo", { list: true, ph: "gemma4:e2b" })}
          <label className="toggle">
            <input
              type="checkbox"
              checked={String(cfg.ollama.think).toLowerCase() === "true"}
              onChange={(e) => set("ollama", "think", e.target.checked ? "true" : "false")}
            />
            <span>
              <b>Razonamiento previo</b>
              <small>Modelos como gemma4 piensan antes de responder. Más elaborado, pero en CPU puede tardar minutos.</small>
            </span>
          </label>
          <button className="ghost mono" onClick={() => save("ollama")}>guardar y probar →</button>
          {probe.ollama && <p className="hint mono">{probe.ollama}</p>}
        </section>

        <section className={used("cloud") ? "" : "dim"}>
          <span className="kicker mono">Nube · API compatible con OpenAI</span>
          <div className="chips">
            {PRESETS.map((p) => (
              <button
                key={p.name}
                className={`chip mono ${cfg.cloud.base_url === p.url ? "on" : ""}`}
                onClick={() => setCfg({ ...cfg, cloud: { ...cfg.cloud, base_url: p.url, chat_model: p.chat } })}
              >
                {p.name}
              </button>
            ))}
          </div>
          {field("cloud", "base_url", "URL base")}
          {field("cloud", "api_key", "API key", { type: "password", ph: "sk-…" })}
          {field("cloud", "chat_model", "Modelo", { list: true })}
          <button className="ghost mono" onClick={() => save("cloud")}>guardar y probar →</button>
          {probe.cloud && <p className="hint mono">{probe.cloud}</p>}
          <p className="hint">Gemini, OpenRouter (modelos «:free») y Groq tienen planes gratuitos. La clave nunca vuelve completa a esta pantalla.</p>
        </section>

        <section>
          <span className="kicker mono">Transcripción</span>
          <p className="hint">
            faster-whisper · <b>{health?.whisper.model ?? "small"}</b>, local. Se cambia con <code>WHISPER_MODEL</code> en
            <code> .env</code>; el modelo se descarga solo la primera vez que llega un audio o video.
          </p>
        </section>

        {(["llamacpp", "ollama", "cloud", "embed"] as Section[]).map((s) => (
          <datalist key={s} id={`models-${s}`}>
            {models[s].map((m) => <option key={m} value={m} />)}
          </datalist>
        ))}

        <footer>
          <span className="mono hint">{msg}</span>
          <button className="primary" disabled={saving} onClick={() => save()}>
            {saving ? "Guardando…" : "Guardar"}
          </button>
        </footer>
      </aside>
    </>
  );
}
