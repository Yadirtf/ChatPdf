# chatpdf · observatorio de documentos

Sube un PDF (máximo **8 páginas**), conviértelo en fragmentos vectorizados dentro de **PostgreSQL + pgvector** y conversa con él usando un **modelo abierto local (Ollama, gratis)** o cualquier **API compatible con OpenAI** con tu API key (de pago o gratuita: OpenAI, Groq, OpenRouter, Mistral, LM Studio…).

La interfaz muestra un **mapa semántico** en vivo: cada fragmento del PDF es una estrella ubicada según su significado, el hilo une los fragmentos en orden de lectura, y cada pregunta aparece como un rombo que traza rayos hacia los pasajes que se usaron para responder. Las citas `p3` de las respuestas iluminan su fragmento en el mapa.

## Arranque rápido (Docker)

```bash
cp .env.example .env        # opcional
docker compose up -d --build
```

- La app queda en **http://localhost:8000**.
- La primera vez, `ollama-pull` descarga `llama3.2:3b` y `nomic-embed-text` (unos 2.3 GB). Sigue el progreso con `docker compose logs -f ollama-pull`.
- ¿Ya tienes Ollama instalado en tu máquina? Pon `OLLAMA_BASE_URL=http://host.docker.internal:11434` en `.env` y levanta solo `docker compose up -d db app`.

## Desarrollo sin Docker

```bash
# 1. Base de datos con pgvector
docker compose up -d db

# 2. Ollama local
ollama pull llama3.2:3b && ollama pull nomic-embed-text

# 3. API (Python 3.11+)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # http://localhost:8000

# 4. Interfaz (Node 20+)
cd frontend
npm install
npm run dev                             # http://localhost:5173 (proxy a /api)
```

## Usar una API key en lugar del modelo local

Abre el botón **motor** (abajo a la izquierda):

1. **Quién responde**: `Local` (Ollama) o `API key`.
2. **Quién indexa (embeddings)**: puede ser distinto del modelo de chat. Groq y OpenRouter tienen planes gratuitos para chat pero no ofrecen embeddings; en ese caso deja los embeddings en `Local`.
3. Elige un preset (OpenAI, Groq, OpenRouter, Mistral, LM Studio), pega tu API key y guarda.

La configuración se guarda en la base de datos y tiene prioridad sobre las variables de entorno. La API key nunca se devuelve completa a la interfaz.

## Cómo funciona

```
PDF ──► pypdf (≤ 8 págs) ──► fragmentos de ~900 caracteres con 150 de solapamiento
    ──► embeddings (Ollama / API) ──► tabla chunks.embedding (pgvector)
Pregunta ──► embedding ──► búsqueda por distancia coseno (<=>) top‑5
         ──► prompt con fragmentos + historial ──► respuesta en streaming con citas [p. N]
```

- Cada documento recuerda el proveedor y modelo de embeddings con el que se indexó; las preguntas se vectorizan con ese mismo modelo, así puedes cambiar de proveedor sin romper los PDFs anteriores.
- La columna `vector` no fija dimensión para admitir modelos distintos (768 en `nomic-embed-text`, 1536 en `text-embedding-3-small`…). Las búsquedas siempre filtran por documento, que con 8 páginas son pocas decenas de fragmentos.
- El mapa usa una proyección PCA 2D de los embeddings calculada al indexar.
- Los PDF escaneados (solo imagen) no tienen texto extraíble: aplícales OCR antes de subirlos.

## API

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/api/health` | Estado de la base de datos y del modelo |
| `GET` / `PUT` | `/api/settings` | Proveedor, URLs, modelos y API key |
| `GET` | `/api/models/{ollama\|openai}` | Modelos disponibles en el proveedor |
| `GET` | `/api/documents` | Lista de PDFs |
| `POST` | `/api/documents` | Sube un PDF (`multipart file`); responde NDJSON con el progreso |
| `DELETE` | `/api/documents/{id}` | Borra el PDF y sus fragmentos |
| `GET` | `/api/documents/{id}/map` | Fragmentos con coordenadas 2D |
| `GET` / `DELETE` | `/api/documents/{id}/messages` | Historial de la conversación |
| `POST` | `/api/documents/{id}/chat` | `{ "message": "..." }`; responde NDJSON: `sources`, `token`…, `done` |

## Variables de entorno

Ver [`.env.example`](.env.example). Las principales: `MAX_PAGES` (8), `MAX_FILE_MB` (20), `CHUNK_SIZE`, `CHUNK_OVERLAP`, `TOP_K`, y las de cada proveedor.
