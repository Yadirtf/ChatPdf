# Guion del video de presentación (6–8 minutos)

Antes de grabar:

- Docker, las dos ventanas de `llama-server` y la API corriendo; la interfaz abierta en `http://localhost:8000`.
- La carpeta `conocimiento/` **vacía** y a mano en el explorador de archivos, con el contenido de ejemplo listo en otra carpeta: el PDF y la imagen de `scripts/muestras.py`, una nota de voz y un video corto (ver «Contenido de ejemplo» en el README).
- Indexa una vez todo antes de grabar para que los modelos ya estén descargados y cargados; luego bórralo de la carpeta. Así en el video la indexación se ve rápida.
- Si tienes una API key gratuita (Gemini u OpenRouter), déjala guardada en **motor** para la parte 6.

| # | Tiempo | Qué mostrar | Qué decir (idea) |
| --- | --- | --- | --- |
| 1 | 0:00–0:40 | La pantalla de inicio de Astrolabio | «ChatPdf ahora es Astrolabio: una base de conocimiento en una carpeta con PDFs, imágenes, audios y videos, que se consulta en lenguaje natural. Todo corre en local con llama.cpp, EmbeddingGemma y PostgreSQL con pgvector.» |
| 2 | 0:40–1:40 | El diagrama del README y `docker compose ps`, las dos ventanas de `llama-server` | Explica el flujo: el PDF se lee y sus imágenes se describen con un modelo de visión; la voz se transcribe con Whisper; los videos además se describen por escenas. Todo termina como texto que EmbeddingGemma convierte en vectores de 768 dimensiones dentro de PostgreSQL con un índice HNSW. |
| 3 | 1:40–3:00 | Arrastra los archivos a la carpeta `conocimiento/` desde el explorador | Se ve cómo aparecen solos en la columna de fuentes, con su etapa: «leyendo páginas», «describiendo imágenes», «transcribiendo», «buscando escenas», «vectorizando». Comenta que la carpeta está vigilada y que el mapa de la derecha se forma con cada fragmento. |
| 4 | 3:00–4:30 | Preguntas sobre imágenes | «¿Qué fuente tuvo mayor participación según el gráfico?» → la cifra solo está en la imagen; muestra la tarjeta con la miniatura del gráfico y abre el PDF en la página 2 desde la cita. Luego «Explica el esquema de almacenamiento de la página 3» (un diagrama dibujado con vectores) y «¿Cuáles son las etapas del ciclo del agua?» (la imagen suelta). |
| 5 | 4:30–5:40 | Preguntas sobre audio y video | «¿Qué se dice sobre … en el audio?» y «¿Qué se ve en el video cuando habla de …?». Pulsa la tarjeta para abrir el video justo en ese minuto. Filtra por el video (clic en la fuente) y pregunta «¿Qué se dice en el minuto 1?». |
| 6 | 5:40–6:40 | Botón **motor** | Muestra los tres proveedores: llama.cpp, Ollama y nube. Cambia **quién responde** a Gemini u OpenRouter (gratuitos) y repite una pregunta; luego vuelve a llama.cpp. Menciona que EmbeddingGemma se mantiene para toda la base y que cambiar de modelo de embeddings revectoriza sin repetir transcripciones ni descripciones. |
| 7 | 6:40–7:30 | Pregunta que cruza fuentes y el mapa | «¿Qué dicen el informe y el audio sobre el almacenamiento con baterías?». Pasa el mouse por las citas: se iluminan los puntos del mapa de distintos colores (texto, imagen, voz). Cierra con el enlace al repositorio. |

Frases cortas que ayudan a explicar decisiones técnicas si el profesor las pide:

- **¿Por qué describir las imágenes en vez de usar embeddings de imagen?** EmbeddingGemma es un modelo de texto; al convertir imágenes, voz y escenas en texto, todo vive en el mismo espacio vectorial y una sola pregunta encuentra cualquier tipo de fuente. Además, si el modelo de chat es multimodal, al responder también mira la imagen original.
- **¿Por qué búsqueda híbrida?** Los vectores encuentran por significado y la búsqueda de texto completo de PostgreSQL encuentra nombres propios y cifras exactas; se combinan con Reciprocal Rank Fusion.
- **¿Dónde corre cada cosa?** La base de datos en Docker; la API, llama.cpp y Whisper en el PC; el modelo de chat puede ser local o en la nube.
