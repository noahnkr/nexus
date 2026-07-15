"""pgvector smoke test: nearest-neighbour ordering works and the HNSW index
exists. Runs over psycopg; all rows roll back with the db fixture."""
from conftest import set_tenant

DIM = 1024


def _basis(i: int) -> str:
    """A 1024-dim unit vector with 1.0 at position i, as a pgvector literal."""
    vals = ["0"] * DIM
    vals[i] = "1"
    return "[" + ",".join(vals) + "]"


def test_hnsw_index_exists(db):
    with db.cursor() as cur:
        cur.execute(
            """select indexdef from pg_indexes
               where schemaname = 'public'
                 and indexname = 'document_chunks_embedding_idx'"""
        )
        row = cur.fetchone()
    assert row is not None, "HNSW index missing"
    assert "hnsw" in row[0].lower()


def test_nearest_neighbour(db, demo_tenant_id):
    set_tenant(db, demo_tenant_id)
    with db.cursor() as cur:
        cur.execute(
            """insert into public.documents (tenant_id, filename, status)
               values (%s, 'vector-smoke.txt', 'ready') returning id""",
            (demo_tenant_id,),
        )
        doc_id = cur.fetchone()[0]

        for idx in range(3):
            cur.execute(
                """insert into public.document_chunks
                     (tenant_id, document_id, chunk_index, chunk_text, embedding)
                   values (%s, %s, %s, %s, %s::vector)""",
                (demo_tenant_id, doc_id, idx, f"chunk {idx}", _basis(idx)),
            )

        # Query vector identical to chunk 1's embedding -> chunk 1 is nearest.
        cur.execute(
            """select chunk_index from public.document_chunks
               where document_id = %s
               order by embedding <=> %s::vector
               limit 1""",
            (doc_id, _basis(1)),
        )
        nearest = cur.fetchone()[0]

    assert nearest == 1
