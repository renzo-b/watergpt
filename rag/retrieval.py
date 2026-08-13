"""Retrieval over the Chroma index.

DELIBERATELY NOT A TOOL. There is no @tool decorator here and no tool
description, and that is the point rather than an omission: the eval harness
calls this function directly, so the only thing varying between two runs is the
chunking config. Register it once the comparison is settled - at that point the
tool description can be written against retrieval that demonstrably works,
instead of against retrieval nobody has measured.

Provenance is not optional. Every chunk carries the document and location it
came from, because the system prompt's grounding rule requires a citation for
any claim about this plant, and a chunk that cannot say where it came from
cannot be cited - it can only be paraphrased, which is the failure the rule
exists to prevent.
"""

from dataclasses import dataclass

from rag import embed, store


@dataclass(frozen=True)
class Chunk:
    """One retrieved chunk. `document` and `location` are what a citation is
    built from; `score` is cosine similarity in [-1, 1], higher is closer."""

    chunk_id: str
    document: str
    location: str
    section: str
    content_type: str
    text: str
    score: float

    def cite(self):
        """The citation string an answer would carry, e.g. '[SOP-14.pdf, p.3]'."""
        return f"[{self.document}, {self.location}]"


def search_plant_docs(
    query,
    plant_id,
    config_id,
    k=10,
    collection=None,
    embedding_model=embed.DEFAULT_MODEL,
    api=None,
):
    """Vector search over one plant's chunks within one ingestion config.

    plant_id and config_id are applied as a `where` filter that Chroma pushes
    into the search rather than as a post-filter on the result set: filtering
    afterwards silently shortens top-k, so a plant with few documents would
    appear to retrieve worse than it does.

    Returns a list of Chunk, closest first, at most k long.
    """
    vector = embed.embed_query(query, model=embedding_model, api=api)

    if collection is None:
        _client, collection = store.open_collection(create=False)

    result = collection.query(
        query_embeddings=[vector],
        n_results=k,
        where=store.scope_filter(config_id, plant_id),
        include=["documents", "metadatas", "distances"],
    )

    # Chroma returns one list per query embedding; there is exactly one here.
    # An empty index yields empty inner lists rather than an error, which is
    # why the harness reports "nothing returned" instead of crashing when a
    # config was never built.
    ids = result["ids"][0] if result["ids"] else []
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    chunks = []
    for index in range(len(ids)):
        meta = metadatas[index] or {}
        chunks.append(
            Chunk(
                chunk_id=meta.get("chunk_id", ids[index]),
                document=meta.get("document", ""),
                location=meta.get("location", ""),
                section=meta.get("section", ""),
                content_type=meta.get("content_type", ""),
                text=documents[index],
                # Cosine distance, so similarity is its complement. This only
                # holds because the collection is pinned to cosine in
                # store.open_collection - under Chroma's default L2 the number
                # would still compute and would mean nothing.
                score=1.0 - float(distances[index]),
            )
        )
    return chunks
