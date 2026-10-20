#!/usr/bin/env bash
# Arranca los dos llama-server que usa Astrolabio (Ctrl+C detiene ambos).
#   8081  EmbeddingGemma 300M  → embeddings de toda la base
#   8080  Gemma 3 4B + visión  → responde y describe imágenes y escenas
# La primera vez descargan los modelos (unos 3,5 GB en total) a la caché de llama.cpp.
#
#   ./scripts/modelos.sh                                   # modelos por defecto
#   CHAT=ggml-org/gemma-3-12b-it-GGUF ./scripts/modelos.sh
set -euo pipefail
CHAT=${CHAT:-ggml-org/gemma-3-4b-it-GGUF}
EMBED=${EMBED:-ggml-org/embeddinggemma-300M-GGUF}

command -v llama-server >/dev/null || { echo "Falta llama-server: brew install llama.cpp (o compílalo desde github.com/ggml-org/llama.cpp)"; exit 1; }

trap 'kill 0' EXIT
llama-server -hf "$EMBED" --embeddings --port 8081 -np 1 -c 2048 -b 2048 -ub 2048 &
llama-server -hf "$CHAT" --port 8080 -np 1 -c 8192 &
echo "Embeddings en http://127.0.0.1:8081 · chat y visión en http://127.0.0.1:8080"
wait
