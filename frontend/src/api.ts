export type Kind = "pdf" | "image" | "audio" | "video";
export type Modality = "text" | "image" | "speech" | "frame";

export type Source = {
  id: string;
  path: string;
  name: string;
  kind: Kind;
  size_bytes: number;
  status: "queued" | "processing" | "ready" | "error";
  error: string | null;
  meta: {
    pages?: number;
    images?: number;
    scanned_pages?: number;
    skipped_images?: number;
    duration?: number;
    frames?: number;
    language?: string;
    width?: number;
    height?: number;
    seconds?: number;
  };
  chunk_count: number;
  progress?: { stage: string; fraction: number | null } | null;
};

export type Library = {
  dir: string;
  queued: number;
  task: { stage: string; done?: number; total?: number; error?: string } | null;
  map_version: number;
};

export type Star = {
  id: number;
  source_id: string;
  idx: number;
  modality: Modality;
  page: number | null;
  t_start: number | null;
  x: number;
  y: number;
  preview: string;
  name: string;
};

export type Hit = {
  n: number;
  chunk_id: number;
  source_id: string;
  name: string;
  kind: Kind;
  modality: Modality;
  page: number | null;
  t_start: number | null;
  t_end: number | null;
  asset_id: number | null;
  score: number;
  content: string;
};

export type Message = {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  sources?: { query: { x: number; y: number } | null; items: Hit[]; model?: string } | null;
  pending?: boolean;
  status?: string;
  error?: string;
};

export type Provider = "llamacpp" | "ollama" | "cloud";

export type ProviderCfg = {
  chat_provider: Provider;
  vision_provider: Provider;
  llamacpp: { base_url: string; chat_model: string };
  ollama: { base_url: string; chat_model: string; think?: string };
  cloud: { base_url: string; api_key: string; chat_model: string; has_key?: boolean };
  embed: { base_url: string; model: string; api_key: string; has_key?: boolean };
};

type Check = { ok: boolean; error?: string };

export type Health = {
  db: boolean;
  limits: { max_pages: number; max_file_mb: number; max_media_minutes: number; extensions: string[] };
  chat: Check & { provider: Provider; model: string };
  vision: Check & { provider: Provider; model: string };
  embed: Check & { model: string; dim: number };
  whisper: { model: string; loaded: boolean };
  library: Library;
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
  return res.status === 204 || res.status === 202 ? (undefined as T) : res.json();
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

const put = (url: string, body: unknown) =>
  fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const fileUrl = (id: string) => `/api/sources/${id}/file`;
export const assetUrl = (id: number) => `/api/assets/${id}`;

export const api = {
  health: () => fetch("/api/health").then(json<Health>),
  settings: () => fetch("/api/settings").then(json<ProviderCfg>),
  saveSettings: (cfg: Partial<ProviderCfg>) => put("/api/settings", cfg).then(json<ProviderCfg>),
  models: (section: string) => fetch(`/api/models/${section}`).then(json<{ models: string[] }>),
  reembed: () => fetch("/api/library/reembed", { method: "POST" }).then(json<void>),
  sources: () => fetch("/api/sources").then(json<{ sources: Source[]; library: Library }>),
  upload: (files: File[]) => {
    const body = new FormData();
    files.forEach((f) => body.append("files", f));
    return fetch("/api/sources", { method: "POST", body }).then(
      json<{ sources: Source[]; rejected: { name: string; error: string }[] }>,
    );
  },
  remove: (id: string) => fetch(`/api/sources/${id}`, { method: "DELETE" }).then(json<void>),
  retry: (id: string) => fetch(`/api/sources/${id}/retry`, { method: "POST" }).then(json<void>),
  map: () => fetch("/api/map").then(json<Star[]>),
  messages: () => fetch("/api/messages").then(json<Message[]>),
  clear: () => fetch("/api/messages", { method: "DELETE" }).then(json<void>),
  async *chat(message: string, scope: string[], signal?: AbortSignal) {
    yield* ndjson(
      await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, scope }),
        signal,
      }),
    );
  },
};

// ---------- Formato ----------

export const fmtTime = (s: number) => {
  const t = Math.floor(s);
  const mm = String(Math.floor(t / 60) % 60).padStart(2, "0");
  const ss = String(t % 60).padStart(2, "0");
  return t >= 3600 ? `${Math.floor(t / 3600)}:${mm}:${ss}` : `${mm}:${ss}`;
};

export const KIND_TAG: Record<Kind, string> = { pdf: "PDF", image: "IMG", audio: "AUD", video: "VID" };
export const MODALITY_LABEL: Record<Modality, string> = {
  text: "texto",
  image: "imagen",
  speech: "voz",
  frame: "escena",
};

/** "pág 3", "01:20" o "" según dónde está el fragmento dentro de su fuente. */
export const locator = (h: { page: number | null; t_start: number | null }) =>
  h.page ? `pág ${h.page}` : h.t_start != null ? fmtTime(h.t_start) : "";

/** Enlace al original en el punto citado: el visor de PDF abre la página y el reproductor salta al minuto. */
export const openAt = (h: { source_id: string; page: number | null; t_start: number | null }) =>
  fileUrl(h.source_id) + (h.page ? `#page=${h.page}` : h.t_start != null ? `#t=${Math.floor(h.t_start)}` : "");
