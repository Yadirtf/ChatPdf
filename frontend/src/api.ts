export type Doc = {
  id: string;
  name: string;
  pages: number;
  size_bytes: number;
  chunk_count: number;
  embed_provider: string;
  embed_model: string;
  created_at: string;
};

export type Star = { id: number; idx: number; page: number; x: number; y: number; preview: string };

export type Source = { chunk_id: number; idx: number; page: number; score: number; content: string };

export type Message = {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  sources?: { query: { x: number; y: number }; items: Source[] } | null;
  pending?: boolean;
  error?: string;
};

export type ProviderCfg = {
  llm_provider: "ollama" | "openai";
  embed_provider: "ollama" | "openai";
  ollama: { base_url: string; chat_model: string; embed_model: string };
  openai: { base_url: string; api_key: string; chat_model: string; embed_model: string; has_key?: boolean };
};

export type Health = {
  db: boolean;
  limits: { max_pages: number; max_file_mb: number };
  llm: { ok: boolean; provider: string; models?: number; error?: string };
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* cuerpo vacío */
    }
    throw new Error(detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/** Lee un stream NDJSON y entrega cada evento según llega. */
async function* ndjson(res: Response): AsyncGenerator<any> {
  if (!res.ok || !res.body) {
    await json(res);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) yield JSON.parse(line);
    }
  }
  if (buf.trim()) yield JSON.parse(buf);
}

export const api = {
  health: () => fetch("/api/health").then(json<Health>),
  settings: () => fetch("/api/settings").then(json<ProviderCfg>),
  saveSettings: (cfg: Partial<ProviderCfg>) =>
    fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }).then(json<ProviderCfg>),
  models: (provider: string) => fetch(`/api/models/${provider}`).then(json<{ models: string[] }>),
  documents: () => fetch("/api/documents").then(json<Doc[]>),
  map: (id: string) => fetch(`/api/documents/${id}/map`).then(json<Star[]>),
  messages: (id: string) => fetch(`/api/documents/${id}/messages`).then(json<Message[]>),
  clear: (id: string) => fetch(`/api/documents/${id}/messages`, { method: "DELETE" }).then(json<void>),
  remove: (id: string) => fetch(`/api/documents/${id}`, { method: "DELETE" }).then(json<void>),
  async *upload(file: File) {
    const body = new FormData();
    body.append("file", file);
    yield* ndjson(await fetch("/api/documents", { method: "POST", body }));
  },
  async *chat(id: string, message: string, signal?: AbortSignal) {
    yield* ndjson(
      await fetch(`/api/documents/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
        signal,
      }),
    );
  },
};
