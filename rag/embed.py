"""Embeddings via OpenAI. The only non-Anthropic model call in the repo.

Anthropic ships no embeddings endpoint, so the vector side of retrieval has to
come from somewhere else. That is the whole reason this file exists; nothing
about the agent's reasoning moves off Claude.

The same model embeds both configs and both sides of every query. That is not a
convenience - a comparison between two chunkings measured through two different
embedding spaces measures nothing.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # optional - the shell environment works just as well
    def load_dotenv(*_args, **_kwargs):
        return False

DEFAULT_MODEL = "text-embedding-3-small"

# Written into every row alongside the vector, so a table can always answer
# "which model produced this" without anyone remembering.
MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

# Well under the API's per-request input cap, and small enough that a failure
# retries cheaply. Raise it if ingestion latency ever matters more than that.
BATCH = 128


def dimension(model):
    try:
        return MODEL_DIMS[model]
    except KeyError:
        raise SystemExit(
            f"unknown embedding model {model!r}. Known: "
            f"{', '.join(sorted(MODEL_DIMS))}. Add it to MODEL_DIMS with its "
            "output dimension - the dimension is part of the table schema, so "
            "it cannot be discovered at write time."
        ) from None


def client():
    load_dotenv()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Embeddings are the one part of this "
            "repo that does not run on Claude - Anthropic has no embeddings "
            "endpoint. Put OPENAI_API_KEY in the .env at the repo root "
            "alongside ANTHROPIC_API_KEY, or set it in your shell."
        )
    from openai import OpenAI

    return OpenAI()


def embed_texts(texts, model=DEFAULT_MODEL, api=None, progress=None):
    """Embed a list of strings, preserving order.

    The API returns results carrying their own index rather than guaranteeing
    order, so they are sorted by it. Trusting arrival order would mis-pair
    vectors with chunks - an error with no symptom except worse retrieval.
    """
    blank = [i for i, t in enumerate(texts) if not t or not t.strip()]
    if blank:
        raise ValueError(
            f"{len(blank)} chunk(s) have empty text (first at index {blank[0]}). "
            "The embeddings API rejects empty input; drop these upstream so the "
            "chunk count in the report stays truthful."
        )

    api = api or client()
    out = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start : start + BATCH]
        response = api.embeddings.create(model=model, input=batch)
        for item in sorted(response.data, key=lambda d: d.index):
            out.append(item.embedding)
        if progress:
            progress(len(out), len(texts))
    return out


def embed_query(text, model=DEFAULT_MODEL, api=None):
    return embed_texts([text], model=model, api=api)[0]
