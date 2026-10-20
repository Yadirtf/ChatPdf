# PostgreSQL y pgvector sin Docker

> Lo recomendado es dejar solo la base de datos en Docker (`docker compose up -d`, puerto 5433) y el resto en tu PC, como explica el [README](../README.md). Esta guía es para quien prefiere instalar PostgreSQL directamente. Todo lo demás (llama.cpp, modelos, API e interfaz) se hace igual que en el README.

| Pieza | Versión | Para qué |
| --- | --- | --- |
| PostgreSQL | 16, 17 o 18 | Base de datos |
| pgvector | 0.8 o superior | Extensión que guarda los vectores y los busca con un índice HNSW |

> **pgvector tiene dos partes.** La **extensión** se instala dentro de PostgreSQL (pasos de abajo). El **driver de Python** (`pgvector` y `psycopg`) se instala solo con `pip install -r requirements.txt`.

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
CREATE USER astrolabio WITH PASSWORD 'astrolabio';
CREATE DATABASE astrolabio OWNER astrolabio;
\c astrolabio
CREATE EXTENSION vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';   -- debe mostrar 0.8.x
```

`CREATE EXTENSION vector` tiene que hacerlo `postgres` porque la extensión exige un superusuario; la app solo la usa.

---

## 3. Apuntar la app a tu PostgreSQL

En `.env` cambia el puerto de `DATABASE_URL` a **5432** (el `.env.example` trae 5433, el de la base en Docker):

```
DATABASE_URL=postgresql://astrolabio:astrolabio@localhost:5432/astrolabio
```

Al arrancar, la API crea las tablas y el índice HNSW sola. Si falta algo, lo dice en la consola.

## Problemas comunes

| Mensaje | Solución |
| --- | --- |
| `No se pudo conectar a PostgreSQL` | El servicio no está corriendo, o `DATABASE_URL` tiene otra contraseña o puerto. En Windows revisa en *Servicios* que `postgresql-x64-17` esté iniciado. |
| `La extensión pgvector no está instalada` / `could not open extension control file` | pgvector no se instaló en la misma versión de PostgreSQL que estás usando. Revisa `PGROOT` y repite el paso 1. |
| `permission denied to create extension "vector"` | Ejecuta `CREATE EXTENSION vector;` como `postgres` dentro de la base `astrolabio` (paso 2). |

Con pgvector anterior a 0.8 todo funciona, pero al filtrar por una fuente la búsqueda HNSW puede devolver menos candidatos; la imagen de Docker ya trae 0.8.
