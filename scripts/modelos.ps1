# Arranca los dos llama-server que usa Astrolabio, cada uno en su ventana.
#   8081  EmbeddingGemma 300M  → embeddings de toda la base
#   8080  Gemma 3 4B + visión  → responde y describe imágenes y escenas
# La primera vez descargan los modelos (unos 3,5 GB en total) a la caché de llama.cpp.
#
#   powershell -ExecutionPolicy Bypass -File scripts\modelos.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\modelos.ps1 -Chat ggml-org/gemma-3-12b-it-GGUF

param(
    [string]$Chat = "ggml-org/gemma-3-4b-it-GGUF",
    [string]$Embed = "ggml-org/embeddinggemma-300M-GGUF"
)

if (-not (Get-Command llama-server -ErrorAction SilentlyContinue)) {
    Write-Error "No se encontró llama-server. Instálalo con: winget install llama.cpp  (y abre una terminal nueva)"
    exit 1
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", `
    "`$host.UI.RawUI.WindowTitle = 'llama.cpp · embeddings :8081'; llama-server -hf $Embed --embeddings --port 8081 -np 1 -c 2048 -b 2048 -ub 2048"
Start-Process powershell -ArgumentList "-NoExit", "-Command", `
    "`$host.UI.RawUI.WindowTitle = 'llama.cpp · chat y visión :8080'; llama-server -hf $Chat --port 8080 -np 1 -c 8192"

Write-Host "Embeddings en http://127.0.0.1:8081  ·  chat y visión en http://127.0.0.1:8080"
