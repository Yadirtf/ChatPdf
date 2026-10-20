# astrolabio · base de conocimiento multimodal

Astrolabio (antes ChatPdf) convierte una carpeta con **PDFs, imágenes, audios y videos cortos** en una base de conocimiento que se consulta en lenguaje natural desde la web. Lee el texto de los PDF, **describe sus imágenes y gráficos**, **transcribe la voz** de audios y videos, **describe las escenas** de los videos y lo guarda todo como vectores de **EmbeddingGemma** (servido por **llama.cpp**) en **PostgreSQL + pgvector**. Cada respuesta cita el fragmento exacto: la página del PDF, la imagen o el minuto del video, y lleva al original en ese punto.

Responde con un modelo local (**llama.cpp** u **Ollama**) o con uno en la nube con tu API key, de pago o gratuito (OpenAI, Gemini, OpenRouter, Groq, Mistral…).

```
conocimiento/   (carpeta vigilada)
 ├─ PDF ──────► texto por página
 │              imágenes · gráficos · páginas escaneadas ──► modelo de visión ──► descripción
 ├─ imagen ─────────────────────────────────────────────────► modelo de visión ──► descripción
 ├─ audio ────► faster-whisper ──► transcripción con marcas de tiempo
 └─ video ────► faster-whisper + fotogramas clave ──────────► modelo de visión ──► descripción
                         │
                         ▼   todo termina como fragmentos de texto
          EmbeddingGemma (llama.cpp) ──► PostgreSQL + pgvector (HNSW) + búsqueda de texto completo
                                                        ▲
pregunta ──► EmbeddingGemma ──► búsqueda híbrida (RRF) ─┘──► chat local o en la nube ──► respuesta con citas [n]
```

La interfaz muestra un **mapa semántico** en vivo: cada fragmento de la base es un punto ubicado según su significado (texto en hueso, imágenes y escenas en verde agua, voz en oro) y cada pregunta traza rayos hacia lo que usó para responder.

## Qué hace, en concreto

- **Carpeta vigilada.** Lo que copies en `conocimiento/` (o subas desde la web) se indexa solo. Si lo borras, sale de la base; si lo mueves o renombras, conserva su índice (se reconoce por su hash). Un único hilo en segundo plano procesa la cola mientras el chat sigue respondiendo.
- **PDF con imágenes.** Con PyMuPDF se extrae el texto de cada página, las imágenes incrustadas (descartando iconos, logos repetidos y fondos), los **gráficos dibujados con vectores** (se recortan y se renderizan) y las **páginas escaneadas** (el modelo de visión las transcribe). Máximo 8 páginas por PDF.
- **Imágenes.** El modelo de visión las describe: tipo de imagen, lo que se ve, el texto legible y, en gráficos y tablas, los datos y la tendencia. La descripción se guarda junto a la imagen por su hash, así nunca se describe dos veces la misma.
- **Audio y video.** `faster-whisper` transcribe en local con marcas de tiempo (sin instalar FFmpeg: lo trae PyAV). De los videos se toman fotogramas clave distintos entre sí (cambios de escena) y se describen con el contexto de lo que se dice en ese momento.
- **Una sola base vectorial.** Todas las fuentes comparten EmbeddingGemma (768 dimensiones) con sus prefijos de tarea (`title: … | text: …` para documentos y `task: search result | query: …` para preguntas), en una columna `vector(768)` con índice **HNSW**.
- **Búsqueda híbrida.** Vecinos por coseno (HNSW) + búsqueda de texto completo en español, fusionadas con Reciprocal Rank Fusion. Se puede consultar toda la base o filtrar por una o varias fuentes.
- **Preguntas que entiende.** «¿qué cifra muestra el gráfico?», «¿de qué trata la página 3?», «¿qué se dice en el minuto 2?», «resume el video», «¿qué contiene la base?». Si una imagen recuperada es relevante y el modelo de chat es multimodal, además **mira la imagen** al responder.
- **Citas navegables.** `[2]` en la respuesta ilumina su fragmento en el mapa; cada tarjeta abre el PDF en su página o el audio/video en su minuto.

## Requisitos

| Pieza | Para qué |
| --- | --- |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | PostgreSQL 16 + pgvector 0.8 (solo la base de datos) |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) (`llama-server`) | EmbeddingGemma y el modelo local de chat y visión |
| Python 3.11+ | API (FastAPI) |
| Node 20+ | Compilar la interfaz |
| [Ollama](https://ollama.com) *(opcional)* | Alternativa local para chat y visión, p. ej. `gemma4:e2b` |

Con 16 GB de RAM funciona en CPU. La primera vez se descargan unos 3,5 GB de modelos de llama.cpp y ~500 MB de Whisper `small`.

## Puesta en marcha (Windows)

Todo se ejecuta desde la carpeta del proyecto, en PowerShell.

**1. Base de datos** (PostgreSQL + pgvector en `localhost:5433`):

```powershell
docker compose up -d
```

> Si venías de ChatPdf, este comando recrea el contenedor con un volumen nuevo (`astrolabio-pgdata`). El antiguo queda intacto; bórralo cuando quieras con `docker volume rm chatpdf_pgdata`.

**2. llama.cpp y los modelos.** Instala llama.cpp y arranca los dos servidores (cada uno abre su ventana; déjalas abiertas):

```powershell
winget install llama.cpp
# abre una terminal nueva para que llama-server esté en el PATH
powershell -ExecutionPolicy Bypass -File scripts\modelos.ps1
```

Eso equivale a:

```powershell
# EmbeddingGemma: embeddings de toda la base (puerto 8081)
llama-server -hf ggml-org/embeddinggemma-300M-GGUF --embeddings --port 8081 -np 1 -c 2048 -b 2048 -ub 2048
# Gemma 3 4B con visión: responde y describe imágenes y escenas (puerto 8080)
llama-server -hf ggml-org/gemma-3-4b-it-GGUF --port 8080 -np 1 -c 8192
```

`-hf` descarga el modelo de Hugging Face la primera vez (y, para Gemma 3, también su proyector de visión `mmproj`). Con GPU puedes pasar a `ggml-org/gemma-3-12b-it-GGUF` con `-Chat`.

**3. Configuración:**

```powershell
copy .env.example .env
```

**4. API** (terminal nueva):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app
```

**5. Interfaz** (una vez, o cada vez que la cambies):

```powershell
cd frontend
npm install
npm run build
```

Abre **http://localhost:8000**. El punto junto a **motor** (abajo a la izquierda) se pone verde cuando la base, los embeddings y el chat responden; si algo falla, un aviso naranja dice qué.

<details>
<summary>macOS / Linux</summary>

```bash
docker compose up -d
brew install llama.cpp            # o compílalo desde github.com/ggml-org/llama.cpp
./scripts/modelos.sh &            # o en otra terminal
cp .env.example .env
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && uvicorn app.main:app
# en otra terminal
cd frontend && npm install && npm run build
```
</details>

## Elegir los modelos

En **motor** se elige por separado:

1. **Quién responde:** `llama.cpp`, `Ollama` o `Nube`.
2. **Quién mira las imágenes** al indexar: cualquiera de los tres, siempre que el modelo sea multimodal.
3. **Embeddings:** la URL del `llama-server` de EmbeddingGemma. Si cambias de modelo, la base se **revectoriza sola** en segundo plano sin volver a transcribir ni describir nada.

| Opción | Configuración | Notas |
| --- | --- | --- |
| llama.cpp (por defecto) | `http://127.0.0.1:8080/v1` | Gemma 3 4B: chat + visión en un solo servidor |
| Ollama | `http://127.0.0.1:11434`, modelo `gemma4:e2b` | `ollama pull gemma4:e2b`; también ve imágenes |
| Nube: Gemini | preset **Gemini**, modelo `gemini-2.5-flash` | Clave gratuita en Google AI Studio |
| Nube: OpenRouter | preset **OpenRouter**, `google/gemma-3-27b-it:free` | Modelos `:free` sin costo |
| Nube: Groq | preset **Groq**, `meta-llama/llama-4-scout-17b-16e-instruct` | Plan gratuito, muy rápido |
| Nube: OpenAI | preset **OpenAI**, `gpt-4o-mini` | De pago |

Un ejemplo útil para la demostración: indexar con visión local (llama.cpp) y responder con Gemini, o al revés. La API key se guarda en la base de datos y nunca vuelve completa a la interfaz.

**Transcripción:** `WHISPER_MODEL` en `.env` (`tiny`, `base`, `small`, `medium`, `large-v3-turbo`). `small` es un buen equilibrio para español en CPU; con `WHISPER_LANGUAGE=es` es algo más rápido.

## Contenido de ejemplo

Para que la demostración muestre cada capacidad, la carpeta debería tener:

| Archivo | Qué comprueba | Pregunta que lo demuestra |
| --- | --- | --- |
| PDF con un gráfico cuyos datos **no** estén en el texto | Preguntas sobre imágenes de un PDF | «¿Qué fuente tuvo mayor participación según el gráfico?» |
| PDF con un diagrama o una página escaneada | Gráficos vectoriales y OCR con visión | «Explica el esquema de la página 3» |
| Una imagen suelta (infografía, foto de pizarra) | Descripción de imágenes | «¿Cuáles son las etapas del ciclo del agua?» |
| Un audio de 1–3 min (nota de voz explicando un tema) | Transcripción | «¿Qué se dice sobre X en el audio?» |
| Un video de 1–3 min con escenas distintas y voz (clase, tutorial) | Transcripción + escenas | «¿Qué se ve en el video cuando habla de X?», «¿qué pasa en el minuto 1?» |

El primer PDF y la imagen se generan con:

```powershell
backend\.venv\Scripts\python scripts\muestras.py
```

Crea `conocimiento/muestras/informe-transicion-energetica.pdf` (3 páginas: texto, un gráfico de barras como imagen y un esquema dibujado con vectores) y `ciclo-del-agua.png`. El audio y el video conviene grabarlos tú (una nota de voz del celular y un video corto de pantalla o de cámara hablando sobre el mismo tema), así la demostración también muestra preguntas que cruzan fuentes: «¿qué dicen el informe y el audio sobre el almacenamiento con baterías?».

## Cómo funciona por dentro

| Archivo | Responsabilidad |
| --- | --- |
| `backend/app/library.py` | Vigila la carpeta (`watchfiles` + revisión periódica), reconcilia altas/bajas/movimientos por hash, cola de indexación, revectorización y mapa 2D (PCA) |
| `backend/app/ingest.py` | Extracción por tipo de fuente y descripciones con caché por hash de imagen |
| `backend/app/pdf.py` | Texto, imágenes incrustadas, gráficos vectoriales (`cluster_drawings`) y páginas escaneadas |
| `backend/app/media.py` | Transcripción con faster-whisper (int8, VAD) y fotogramas clave con PyAV |
| `backend/app/providers.py` | EmbeddingGemma con prefijos de tarea; chat y visión para llama.cpp, Ollama y APIs compatibles con OpenAI |
| `backend/app/rag.py` | Búsqueda híbrida HNSW + texto completo con RRF, selección por página/minuto/panorama, prompt con fragmentos numerados |
| `backend/app/db.py` | Esquema: `sources`, `chunks` (`vector(768)`, HNSW, `tsvector` generado), `assets`, `messages` |

Decisiones que vale la pena conocer:

- **Todo se vuelve texto antes de vectorizar.** Las imágenes y escenas se describen y la voz se transcribe; así un solo modelo de embeddings (EmbeddingGemma, que es de texto) ubica PDFs, imágenes, audios y videos en el mismo espacio, y una pregunta los encuentra a todos.
- **La extracción cara se hace una vez.** Las descripciones se guardan por hash de la imagen y las transcripciones en los fragmentos; cambiar de modelo de embeddings solo recalcula vectores.
- **Si cambia `EMBED_DIM`**, la columna se redimensiona al arrancar y la base se revectoriza sola.
- **Si el modelo de chat no acepta imágenes**, la respuesta se reintenta solo con sus descripciones.

## API

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/api/health` | Estado de la base, embeddings, chat, visión, Whisper y la carpeta |
| `GET` / `PUT` | `/api/settings` | Proveedores, URLs, modelos y API keys |
| `GET` | `/api/models/{llamacpp\|ollama\|cloud\|embed}` | Modelos disponibles |
| `GET` | `/api/sources` | Fuentes con su estado y progreso |
| `POST` | `/api/sources` | Sube archivos (`multipart files`) a la carpeta |
| `DELETE` | `/api/sources/{id}` | Borra el archivo y su índice |
| `POST` | `/api/sources/{id}/retry` | Reintenta una fuente con error |
| `GET` | `/api/sources/{id}/file` | El original (admite `Range`: el reproductor salta al minuto) |
| `POST` | `/api/library/scan` · `/api/library/reembed` | Revisa la carpeta ahora · revectoriza toda la base |
| `GET` | `/api/map` | Fragmentos con coordenadas 2D |
| `GET` | `/api/assets/{id}` | Imagen (página, figura o fotograma) |
| `GET` / `DELETE` | `/api/messages` | Historial |
| `POST` | `/api/chat` | `{ "message": "...", "scope": [ids] }`; NDJSON: `status`, `sources`, `token`…, `done` o `error` |

## Problemas comunes

| Síntoma | Solución |
| --- | --- |
| Aviso «El servidor de embeddings no responde» | Falta la ventana de `llama-server` en el puerto 8081 (paso 2). |
| `input is too large to process` | Arranca el servidor de embeddings con `-b 2048 -ub 2048`, como en `scripts/modelos.ps1`. |
| Una imagen queda en error «no devolvió ninguna descripción» | El modelo de visión no es multimodal. En llama.cpp usa un repo con `mmproj` (p. ej. `ggml-org/gemma-3-4b-it-GGUF`) o elige Ollama `gemma4:e2b` o la nube en **quién mira las imágenes**. Luego pulsa ↻ en la fuente. |
| La primera transcripción tarda | Se está descargando el modelo de Whisper; las siguientes son inmediatas. |
| `No se pudo conectar a PostgreSQL` | `docker compose up -d` y revisa que `DATABASE_URL` use el puerto 5433. Sin Docker: [docs/instalacion-local.md](docs/instalacion-local.md). |
| En Docker los archivos nuevos tardan en aparecer | En carpetas montadas desde Windows los eventos no llegan al contenedor: se revisa cada `SCAN_INTERVAL` segundos. |

## Video de presentación

El guion paso a paso para grabar la demostración está en [docs/guion-video.md](docs/guion-video.md).
