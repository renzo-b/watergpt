"""Chroma storage. One collection, on disk, no server.

Chosen over Postgres/pgvector for one reason that matters more than any of
pgvector's advantages here: pgvector is a compiled extension with no prebuilt
Windows package, so standing it up means either a Docker daemon or a Visual
Studio build chain before a single chunk can be indexed. Chroma is a pip
install and a directory. This is a measurement harness, not the production
store - the cost of being wrong about it is a re-index, so simplicity wins.

Two guarantees that pgvector enforced in the schema have to be enforced here
instead, because Chroma has no constraints and no column types:

  content_type   was a CHECK constraint, now validated before every write
  one embedding model per collection
                 was vector(dim) in the DDL, now checked against what is
                 already stored

Both are checked rather than trusted. A chunk with an unexpected content_type
or a vector from a different model is a silent retrieval bug - the data looks
fine and only the answers are worse - which is exactly the kind of thing worth
paying a few lines to make loud.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "chroma"
COLLECTION = "chunks"

# Both configs live in ONE collection, told apart by a config_id filter at query
# time, exactly as they were told apart by a WHERE clause before. Separate
# collections would be easier to write and would quietly weaken the comparison:
# two collections can drift in distance metric or dimensionality without
# anything complaining, and then the two configs are no longer measured the
# same way.
CONTENT_TYPES = ("prose", "table", "interpreted_statement")

# Chroma defaults to squared L2. The retrieval layer reports similarity as
# 1 - distance, which is only meaningful under cosine, so the space is set
# explicitly at creation and verified on every open.
SPACE = "cosine"


def default_path():
    return Path(os.environ.get("WATERGPT_CHROMA_PATH", DEFAULT_PATH))


def open_collection(path=None, create=True):
    """Open (or create) the chunks collection on disk.

    Imported here rather than at module scope: chromadb pulls in onnxruntime
    and a stack of telemetry packages, and scripts/eval_retrieval.py's matcher
    tests import that module without needing any of it.
    """
    import chromadb
    from chromadb.config import Settings

    store_path = Path(path or default_path())
    if not create and not store_path.exists():
        raise SystemExit(
            f"no index at {store_path}. Build one first:\n"
            "  python scripts/build_index.py --input documents/test "
            "--config-id triplet_tables --plant-id demo --table-serializer triplet"
        )
    store_path.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(
        path=str(store_path),
        # Off by default here. This indexes plant operating documents; whether
        # any of it leaves the machine should be a decision, not a default.
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=COLLECTION, metadata={"hnsw:space": SPACE}
    )

    space = (collection.metadata or {}).get("hnsw:space")
    if space != SPACE:
        raise SystemExit(
            f"the '{COLLECTION}' collection at {store_path} uses "
            f"{space!r} distance, not {SPACE!r}. Scores would not mean what "
            "the harness reports. Delete that directory and rebuild both "
            "configs from scratch."
        )
    return client, collection


def scope_filter(config_id, plant_id):
    return {
        "$and": [
            {"plant_id": {"$eq": plant_id}},
            {"config_id": {"$eq": config_id}},
        ]
    }


def check_embedding_model(collection, model):
    """Refuse to mix embedding models within one collection.

    Comparing two chunking configs through two different embedding spaces
    measures the embeddings, not the chunking. pgvector caught this as a
    dimension mismatch on insert; nothing here would catch it at all, since
    two models of the same width would be accepted silently.
    """
    existing = collection.get(limit=1, include=["metadatas"])
    metadatas = existing.get("metadatas") or []
    if not metadatas:
        return
    stored = metadatas[0].get("embedding_model")
    if stored and stored != model:
        raise SystemExit(
            f"this index was built with {stored!r} but this run uses {model!r}. "
            "Every config in one index must share an embedding model. Rerun "
            f"with --embedding-model {stored}, or delete "
            f"{default_path()} and rebuild every config."
        )


def write_chunks(collection, rows, config_id, plant_id, batch=1000):
    """Upsert this run's rows, then drop rows of this scope it did not produce.

    Returns (written, deleted). The delete is what keeps a rerun honest:
    without it a chunk that disappeared because the chunker changed its mind
    stays in the index forever and keeps being retrieved, and the per-chunk
    diff between two runs of the same config shows nothing wrong.
    """
    bad = {r["content_type"] for r in rows} - set(CONTENT_TYPES)
    if bad:
        raise SystemExit(
            f"unknown content_type(s): {', '.join(sorted(bad))}. "
            f"Expected one of {', '.join(CONTENT_TYPES)}."
        )

    def record_id(row):
        # chunk_id is unique within a config and plant, not globally; Chroma
        # ids are global to the collection, so the scope is part of the id.
        return f"{row['config_id']}|{row['plant_id']}|{row['chunk_id']}"

    for start in range(0, len(rows), batch):
        window = rows[start : start + batch]
        collection.upsert(
            ids=[record_id(r) for r in window],
            embeddings=[r["embedding"] for r in window],
            documents=[r["text"] for r in window],
            metadatas=[
                {
                    "chunk_id": r["chunk_id"],
                    "config_id": r["config_id"],
                    "plant_id": r["plant_id"],
                    "document": r["document"],
                    "location": r["location"],
                    "section": r["section"],
                    "content_type": r["content_type"],
                    "embedding_model": r["embedding_model"],
                }
                for r in window
            ],
        )

    present = set(
        collection.get(where=scope_filter(config_id, plant_id), include=[])["ids"]
    )
    stale = sorted(present - {record_id(r) for r in rows})
    if stale:
        collection.delete(ids=stale)
    return len(rows), len(stale)


def scope_summary(collection, config_id, plant_id):
    """(content_type, count) pairs for the end-of-run report."""
    got = collection.get(where=scope_filter(config_id, plant_id), include=["metadatas"])
    counts = {}
    for meta in got.get("metadatas") or []:
        key = meta.get("content_type", "?")
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items())
