import { useEffect, useState } from "react";
import { api, type ProviderCfg } from "../api";

type Props = { open: boolean; onClose: () => void; onSaved: (cfg: ProviderCfg) => void };

const PRESETS = [
  { name: "OpenAI", url: "https://api.openai.com/v1", chat: "gpt-4o-mini", embed: "text-embedding-3-small" },
  { name: "Groq", url: "https://api.groq.com/openai/v1", chat: "llama-3.3-70b-versatile", embed: "" },
  { name: "OpenRouter", url: "https://openrouter.ai/api/v1", chat: "meta-llama/llama-3.3-70b-instruct:free", embed: "" },
  { name: "Mistral", url: "https://api.mistral.ai/v1", chat: "mistral-small-latest", embed: "mistral-embed" },
  { name: "LM Studio", url: "http://localhost:1234/v1", chat: "", embed: "" },
];

type Prov = "ollama" | "openai";

export default function Settings({ open, onClose, onSaved }: Props) {
  const [cfg, setCfg] = useState<ProviderCfg | null>(null);
  const [models, setModels] = useState<Record<Prov, string[]>>({ ollama: [], openai: [] });
  const [probe, setProbe] = useState<Record<Prov, string>>({ ollama: "", openai: "" });
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

  const loadModels = async (p: Prov) => {
    setProbe((s) => ({ ...s, [p]: "probando…" }));
    try {
      const { models } = await api.models(p);
      setModels((m) => ({ ...m, [p]: models }));
      setProbe((s) => ({ ...s, [p]: `conectado · ${models.length} modelos` }));
    } catch (e: any) {
      setProbe((s) => ({ ...s, [p]: e.message }));
    }
  };

  if (!cfg) return <aside className={`sheet ${open ? "open" : ""}`} aria-hidden={!open} />;

  const set = (p: Prov, k: string, v: string) => setCfg({ ...cfg, [p]: { ...cfg[p], [k]: v } });

  const save = async (andProbe = false) => {
    setSaving(true);
    try {
      const saved = await api.saveSettings(cfg);
      setCfg(saved);
      onSaved(saved);
      setMsg("Guardado.");
      if (andProbe) await loadModels(saved.llm_provider);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setSaving(false);
    }
  };

  const field = (p: Prov, k: string, label: string, opts: { type?: string; list?: boolean; ph?: string } = {}) => (
    <label className="field">
      <span className="mono">{label}</span>
      <input
        type={opts.type ?? "text"}
        value={(cfg[p] as any)[k] ?? ""}
        placeholder={opts.ph}
        list={opts.list ? `models-${p}` : undefined}
        onChange={(e) => set(p, k, e.target.value)}
        onFocus={(e) => k === "api_key" && e.target.value.startsWith("•") && set(p, k, "")}
        spellCheck={false}
        autoComplete="off"
      />
    </label>
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
          <div className="seg">
            {(["ollama", "openai"] as Prov[]).map((p) => (
              <button key={p} className={cfg.llm_provider === p ? "on" : ""} onClick={() => setCfg({ ...cfg, llm_provider: p })}>
                <b>{p === "ollama" ? "Local" : "API key"}</b>
                <small className="mono">{p === "ollama" ? "Ollama · gratis" : "OpenAI-compatible"}</small>
              </button>
            ))}
          </div>
        </section>

        <section>
          <span className="kicker mono">02 — Quién indexa (embeddings)</span>
          <div className="seg">
            {(["ollama", "openai"] as Prov[]).map((p) => (
              <button key={p} className={cfg.embed_provider === p ? "on" : ""} onClick={() => setCfg({ ...cfg, embed_provider: p })}>
                <b>{p === "ollama" ? "Local" : "API key"}</b>
                <small className="mono">{p === "ollama" ? cfg.ollama.embed_model : cfg.openai.embed_model || "—"}</small>
              </button>
            ))}
          </div>
          <p className="hint">Aplica a los PDFs que subas después. Cada documento recuerda con qué modelo se indexó.</p>
        </section>

        <section className={cfg.llm_provider === "ollama" || cfg.embed_provider === "ollama" ? "" : "dim"}>
          <span className="kicker mono">Ollama</span>
          {field("ollama", "base_url", "URL")}
          {field("ollama", "chat_model", "Modelo de chat", { list: true, ph: "llama3.2:3b" })}
          {field("ollama", "embed_model", "Modelo de embeddings", { list: true, ph: "nomic-embed-text" })}
          <button className="ghost mono" onClick={() => loadModels("ollama")}>probar conexión →</button>
          {probe.ollama && <p className="hint mono">{probe.ollama}</p>}
        </section>

        <section className={cfg.llm_provider === "openai" || cfg.embed_provider === "openai" ? "" : "dim"}>
          <span className="kicker mono">API compatible con OpenAI</span>
          <div className="chips">
            {PRESETS.map((p) => (
              <button
                key={p.name}
                className={`chip mono ${cfg.openai.base_url === p.url ? "on" : ""}`}
                onClick={() =>
                  setCfg({
                    ...cfg,
                    openai: { ...cfg.openai, base_url: p.url, chat_model: p.chat || cfg.openai.chat_model, embed_model: p.embed || cfg.openai.embed_model },
                  })
                }
              >
                {p.name}
              </button>
            ))}
          </div>
          {field("openai", "base_url", "URL base")}
          {field("openai", "api_key", "API key", { type: "password", ph: "sk-…" })}
          {field("openai", "chat_model", "Modelo de chat", { list: true })}
          {field("openai", "embed_model", "Modelo de embeddings", { list: true })}
          <button className="ghost mono" onClick={() => save().then(() => loadModels("openai"))}>guardar y probar →</button>
          {probe.openai && <p className="hint mono">{probe.openai}</p>}
          <p className="hint">Groq y OpenRouter tienen planes gratuitos para chat, pero no ofrecen embeddings: combínalos con embeddings locales.</p>
        </section>

        {(["ollama", "openai"] as Prov[]).map((p) => (
          <datalist key={p} id={`models-${p}`}>
            {models[p].map((m) => <option key={m} value={m} />)}
          </datalist>
        ))}

        <footer>
          <span className="mono hint">{msg}</span>
          <button className="primary" disabled={saving} onClick={() => save(true)}>
            {saving ? "Guardando…" : "Guardar"}
          </button>
        </footer>
      </aside>
    </>
  );
}
