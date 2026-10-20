from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

# Una sola base vectorial para todas las fuentes: la columna tiene dimensión fija y un
# índice HNSW, así una consulta recorre PDFs, imágenes, audios y videos a la vez.
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sources (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    path        TEXT NOT NULL UNIQUE,      -- relativa a la carpeta de conocimiento
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- pdf | image | audio | video
    sha256      TEXT NOT NULL,
    size_bytes  BIGINT NOT NULL,
    mtime_ns    BIGINT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',   -- queued | processing | ready | error
    error       TEXT,
    meta        JSONB NOT NULL DEFAULT '{}',
    chunk_count INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Imágenes de PDFs, imágenes sueltas y fotogramas de video. Se identifican por su hash:
-- la descripción del modelo de visión se reutiliza si la misma imagen vuelve a aparecer.
CREATE TABLE IF NOT EXISTS assets (
    id           BIGSERIAL PRIMARY KEY,
    sha256       TEXT NOT NULL UNIQUE,
    mime         TEXT NOT NULL,
    width        INT NOT NULL,
    height       INT NOT NULL,
    data         BYTEA NOT NULL,
    description  TEXT,
    described_by TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    source_id   UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    idx         INT NOT NULL,
    modality    TEXT NOT NULL,             -- text | image | speech | frame
    page        INT,
    t_start     REAL,
    t_end       REAL,
    content     TEXT NOT NULL,
    asset_id    BIGINT REFERENCES assets(id) ON DELETE SET NULL,
    embedding   vector({dim}),
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED,
    x           REAL NOT NULL DEFAULT 0,
    y           REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_source_idx ON chunks(source_id, idx);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING gin(tsv);

CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    sources     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL
);
"""

HNSW = "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"

pool: ConnectionPool | None = None
# pgvector >= 0.8 permite que HNSW siga buscando cuando un filtro descarta candidatos.
iterative_scan = False


def _configure(conn):
    register_vector(conn)


def _sync_dimension(c) -> None:
    """Si EMBED_DIM cambió, la columna se redimensiona y los vectores quedan pendientes de recalcular.

    El texto extraído, las descripciones y las transcripciones se conservan: solo se vuelve a vectorizar.
    """
    current = c.execute(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    ).fetchone()[0]
    if current != settings.embed_dim:
        c.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw")
        c.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({settings.embed_dim}) USING NULL")
    c.execute(HNSW)


def init_db():
    global pool, iterative_scan
    try:
        with psycopg.connect(settings.database_url, autocommit=True) as c:
            c.execute(SCHEMA.replace("{dim}", str(settings.embed_dim)))
            _sync_dimension(c)
            version = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()[0]
    except (psycopg.errors.FeatureNotSupported, psycopg.errors.UndefinedFile,
            psycopg.errors.InsufficientPrivilege) as e:
        raise SystemExit(
            "\n✗ La extensión pgvector no está instalada o el usuario no puede activarla.\n"
            "  Usa la base de Docker (docker compose up -d) o instálala y ejecuta como superusuario:\n"
            "      CREATE EXTENSION vector;\n"
            f"  Detalle: {e}\n"
        ) from e
    except psycopg.OperationalError as e:
        raise SystemExit(
            f"\n✗ No se pudo conectar a PostgreSQL ({settings.database_url}).\n"
            f"  ¿Está corriendo «docker compose up -d»? Detalle: {e}\n"
        ) from e
    iterative_scan = tuple(int(p) for p in version.split(".")[:2]) >= (0, 8)
    pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        configure=_configure,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def close_db():
    if pool:
        pool.close()


@contextmanager
def conn():
    assert pool is not None, "La base de datos no está inicializada"
    with pool.connection() as c:
        yield c
