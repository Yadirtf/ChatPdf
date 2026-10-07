from contextlib import contextmanager

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    pages       INT NOT NULL,
    size_bytes  INT NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    embed_provider TEXT NOT NULL,
    embed_model    TEXT NOT NULL,
    embed_dim      INT NOT NULL DEFAULT 0,
    projection  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- La columna vector no fija dimensión: cada documento guarda la de su modelo de embeddings,
-- así se puede cambiar de proveedor sin migrar. Las búsquedas siempre filtran por documento.
CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx         INT NOT NULL,
    page        INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector NOT NULL,
    x           REAL NOT NULL DEFAULT 0,
    y           REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks(document_id);

CREATE TABLE IF NOT EXISTS messages (
    id          BIGSERIAL PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    sources     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_document_idx ON messages(document_id, id);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL
);
"""

pool: ConnectionPool | None = None


def _configure(conn):
    register_vector(conn)


def init_db():
    global pool
    # La extensión debe existir antes de registrar el tipo vector en el pool.
    import psycopg

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(SCHEMA)
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
