# chatpdf · observatorio de documentos

Sube un PDF (máximo **8 páginas**), conviértelo en fragmentos vectorizados dentro de **PostgreSQL + pgvector** y conversa con él usando un **modelo abierto local (Ollama, gratis)** o cualquier **API compatible con OpenAI** con tu API key (de pago o gratuita: OpenAI, Groq, OpenRouter, Mistral, LM Studio…).

La interfaz muestra un **mapa semántico** en vivo: cada fragmento del PDF es una estrella ubicada según su significado, el hilo une los fragmentos en orden de lectura, y cada pregunta aparece como un rombo que traza rayos hacia los pasajes que se usaron para responder. Las citas `p3` de las respuestas iluminan su fragmento en el mapa.

## Arranque rápido: base de datos en Docker, app y Ollama en tu PC

Necesitas Docker, [Ollama](https://ollama.com), Python 3.11+ y Node 20+.

**1. Modelos de Ollama** (una sola vez). `gemma4:e2b` responde; `nomic-embed-text` convierte el PDF en vectores.

```bash
ollama pull gemma4:e2b
ollama pull nomic-embed-text
```

**2. Base de datos** (PostgreSQL 16 + pgvector, ya incluido en la imagen) en `localhost:5433`:

```bash
docker compose up -d
```

**3. Configuración** en la raíz del proyecto:

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

**4. API** (terminal 1):

```bash
cd backend
python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload
# macOS / Linux:
source .venv/bin/activate && pip install -r requirements.txt && uvicorn app.main:app --reload
```

**5. Interfaz** (terminal 2, una vez o cada vez que la cambies):

```bash
cd frontend
npm install
npm run build
```

Abre **http://localhost:8000**. Para editar la interfaz con recarga en vivo, usa `npm run dev` y abre http://localhost:5173.

> Si en el botón **motor** guardaste antes otra URL de Ollama (por ejemplo `host.docker.internal`), vuelve a poner `http://localhost:11434`: lo guardado ahí tiene prioridad sobre `.env`.

### Otras formas de ejecutarlo

- **Todo en Docker menos Ollama:** `docker compose --profile app up -d --build`. La app usa el Ollama de tu PC vía `http://host.docker.internal:11434`. En Linux, Ollama debe escuchar en `0.0.0.0` (`OLLAMA_HOST=0.0.0.0`).
- **Todo en Docker:** `DOCKER_OLLAMA_URL=http://ollama:11434 docker compose --profile app --profile ollama up -d --build`.
- **Sin Docker** (PostgreSQL instalado en tu PC): sigue [docs/instalacion-local.md](docs/instalacion-local.md), que incluye cómo instalar pgvector, y usa el puerto `5432` en `DATABASE_URL`.

## Preguntar al documento

El chat responde preguntas libres y explica a partir del texto del PDF, citando páginas como `p3`:

- **Por página:** «¿De qué trata la página 3?», «explica la tercera página», «compara las págs. 2 y 4», «qué dice la última página». Se le da al modelo el texto completo de esas páginas.
- **Generales:** «resume el documento», «¿de qué trata?». Si el PDF es corto, el modelo recibe el documento entero; si no, una selección de todas las páginas.
- **Específicas:** «¿qué cifras menciona sobre X?». Se combinan búsqueda semántica (pgvector) y por palabras clave (búsqueda de texto de PostgreSQL).

Mientras el modelo trabaja, la respuesta muestra qué está haciendo («leyendo la página 3», «razonando») y los segundos que lleva. En CPU, la primera respuesta puede tardar porque Ollama carga el modelo en memoria; las siguientes son más rápidas.

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

Pregunta ──► ¿menciona páginas? ──► texto completo de esas páginas
         ──► ¿es general o el PDF es corto? ──► documento completo (o una muestra de cada página)
         ──► si no ──► top‑6 por fusión de búsqueda vectorial (<=>) y por palabras clave (tsvector)
         ──► prompt con el texto agrupado por página + historial ──► respuesta en streaming con citas [p. N]
```

- Cada documento recuerda el proveedor y modelo de embeddings con el que se indexó; las preguntas se vectorizan con ese mismo modelo, así puedes cambiar de proveedor sin romper los PDFs anteriores.
- La columna `vector` no fija dimensión para admitir modelos distintos (768 en `nomic-embed-text`, 1536 en `text-embedding-3-small`…). Las búsquedas siempre filtran por documento, que con 8 páginas son pocas decenas de fragmentos.
- Con Ollama se pide un contexto de 8192 tokens (`OLLAMA_NUM_CTX`), porque el de por defecto recorta el texto del PDF, y el modelo queda cargado 30 minutos entre preguntas.
- El razonamiento previo de modelos como `gemma4` está apagado por defecto (`OLLAMA_THINK=false`): en CPU puede tardar minutos antes de escribir la primera palabra. Se activa en el botón **motor**.
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
| `POST` | `/api/documents/{id}/chat` | `{ "message": "..." }`; responde NDJSON: `status`, `sources`, `token`…, `done` o `error` |

## Variables de entorno

Ver [`.env.example`](.env.example). Las principales: `MAX_PAGES` (8), `MAX_FILE_MB` (20), `CHUNK_SIZE`, `CHUNK_OVERLAP`, `TOP_K`, `FULL_DOC_CHARS`, `OLLAMA_NUM_CTX`, `OLLAMA_THINK`, y las de cada proveedor.
