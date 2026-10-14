# Ejecutar ChatPdf sin Docker

> ¿Prefieres no instalar PostgreSQL? La forma más simple es tener solo la base de datos en Docker (`docker compose up -d`) y la app y Ollama en tu PC: ver el [README](../README.md#arranque-rápido-base-de-datos-en-docker-app-y-ollama-en-tu-pc). Esta guía es para instalar también PostgreSQL y pgvector en tu PC.

Esta guía levanta todo en tu máquina: PostgreSQL con pgvector, Ollama, la API en Python y la interfaz. Docker sigue funcionando igual (`docker compose up -d --build`) para cuando lo necesites.

Necesitas:

| Pieza | Versión | Para qué |
| --- | --- | --- |
| PostgreSQL | 16, 17 o 18 | Base de datos |
| pgvector | 0.8 o superior | Extensión de PostgreSQL que guarda y busca vectores |
| Ollama | reciente | Modelo de chat (`gemma4:e2b`) y de embeddings (`nomic-embed-text`) |
| Python | 3.11 o superior | API (FastAPI) |
| Node.js | 20 o superior | Compilar la interfaz |
| Git | cualquiera | Descargar pgvector en Windows |

> **pgvector tiene dos partes.** La **extensión** se instala dentro de PostgreSQL (pasos de abajo). El **driver de Python** (`pgvector` y `psycopg`) se instala solo con `pip install -r requirements.txt`, así que no tienes que hacer nada aparte para él.

---

## 1. PostgreSQL + extensión pgvector

### Windows

1. Instala PostgreSQL con el instalador de EDB: <https://www.postgresql.org/download/windows/>. Recuerda la contraseña que le pongas al usuario `postgres`. En los ejemplos de abajo se usa la versión **17**; si instalaste otra, cambia el número.
2. Instala **Visual Studio 2022 Build Tools** (<https://visualstudio.microsoft.com/es/downloads/>, sección "Herramientas para Visual Studio") y marca la carga de trabajo **"Desarrollo para el escritorio con C++"**. Es necesario porque pgvector se compila en Windows.
3. Abre el menú Inicio, busca **"x64 Native Tools Command Prompt for VS 2022"** y ábrelo con clic derecho, **Ejecutar como administrador**. Luego ejecuta:

   ```bat
   set "PGROOT=C:\Program Files\PostgreSQL\17"
   cd %TEMP%
   git clone --branch v0.8.1 https://github.com/pgvector/pgvector.git
   cd pgvector
   nmake /F Makefile.win
   nmake /F Makefile.win install
   ```

   Si `nmake` no se reconoce, no estás en la consola "x64 Native Tools". Si ves "Acceso denegado" al instalar, no la abriste como administrador.

### macOS

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17
```

(Con **Postgres.app** no hace falta nada más: ya trae pgvector.)

### Linux (Ubuntu / Debian)

```bash
sudo apt install postgresql postgresql-17-pgvector   # usa el número de tu versión de PostgreSQL
```

Si tu distribución no trae el paquete, agrega el repositorio oficial de PostgreSQL (<https://www.postgresql.org/download/linux/>) o compílalo con `make && sudo make install` desde el repositorio de pgvector.

---

## 2. Crear la base de datos y activar pgvector

Abre `psql` como el superusuario `postgres` (en Windows: menú Inicio, **SQL Shell (psql)**, y Enter en todo menos la contraseña):

```sql
CREATE USER chatpdf WITH PASSWORD 'chatpdf';
CREATE DATABASE chatpdf OWNER chatpdf;
\c chatpdf
CREATE EXTENSION vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';   -- debe mostrar 0.8.x
```

`CREATE EXTENSION vector` tiene que hacerlo `postgres` porque la extensión exige un superusuario; la app solo la usa.

---

## 3. Ollama

Ya tienes `gemma4:e2b` para responder. Falta un modelo de **embeddings**, que convierte los fragmentos del PDF en vectores. Los modelos de chat como Gemma no sirven para eso.

```bash
ollama pull nomic-embed-text
ollama list        # deben aparecer gemma4:e2b y nomic-embed-text
```

Ollama tiene que estar abierto (icono en la barra de tareas, o `ollama serve`).

---

## 4. Configuración

En la raíz del proyecto:

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Cambia el puerto de `DATABASE_URL` a **5432** (el `.env.example` trae 5433, el de la base en Docker). El resto ya apunta a tu instalación local (usuario y contraseña `chatpdf`, `gemma4:e2b` y `nomic-embed-text`). Si usaste otra contraseña o puerto, edita `DATABASE_URL`.

---

## 5. API (Python)

**Windows (PowerShell):**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

**macOS / Linux:**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Al arrancar, la API crea las tablas sola. Si falta algo, lo dice en la consola (por ejemplo, que no encuentra PostgreSQL o que falta activar pgvector).

---

## 6. Interfaz

En otra terminal:

```bash
cd frontend
npm install
npm run build
```

Abre **<http://localhost:8000>**: la API sirve la interfaz compilada.

¿Vas a modificar la interfaz? Usa `npm run dev` y abre <http://localhost:5173>. Los cambios se ven al instante y las llamadas a `/api` se redirigen a la API en el puerto 8000.

---

## Problemas comunes

| Mensaje | Solución |
| --- | --- |
| `No se pudo conectar a PostgreSQL` | El servicio no está corriendo, o `DATABASE_URL` tiene otra contraseña o puerto. En Windows revisa en *Servicios* que `postgresql-x64-17` esté iniciado. |
| `La extensión pgvector no está instalada` / `could not open extension control file` | pgvector no se instaló en la misma versión de PostgreSQL que estás usando. Revisa `PGROOT` y repite el paso 1. |
| `permission denied to create extension "vector"` | Ejecuta `CREATE EXTENSION vector;` como `postgres` dentro de la base `chatpdf` (paso 2). |
| Banner naranja `Falta descargar: ollama pull …` | Ejecuta ese comando y recarga la página. |
| `No se pudo conectar con ollama` | Abre Ollama, o revisa `OLLAMA_BASE_URL` en `.env`. |
| La respuesta tarda mucho | Es normal en CPU con modelos locales. `gemma4:e2b` es de los más livianos. |

## Volver a Docker más adelante

- **Solo la base de datos en Docker:** `docker compose up -d` y usa el puerto `5433` en `DATABASE_URL`.
- **También la app en Docker:** `docker compose --profile app up -d --build`. La app usa el Ollama de tu PC vía `http://host.docker.internal:11434`. La URL guardada en el botón **motor** se adapta sola al modo en que corre la app. En Linux, Ollama debe escuchar en `0.0.0.0` (`OLLAMA_HOST=0.0.0.0`).
- La base de datos de Docker es distinta de la local, así que los PDFs indexados en una no aparecen en la otra.
