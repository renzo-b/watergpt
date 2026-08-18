"""Document upload pipeline: any file -> catalogued, interpreted, in context.

    from ingest import catalogue, read_manifest

    catalogue()        -> every ingested document, described, for context
    read_manifest()    -> the full DocEntry records behind it

Ingestion is a separate step and lives in ingest.pipeline, which is a CLI:

    python -m ingest.pipeline --input <file or directory>

The re-exports below are resolved lazily. Importing ingest.pipeline eagerly
here would make `python -m ingest.pipeline` load the module twice - once via
this package import, once as __main__ - which runpy warns about.
"""

from ingest.schema import Component, DocEntry

__all__ = [
    "Component",
    "DocEntry",
    "catalogue",
    "ingest_file",
    "read_manifest",
]


def __getattr__(name):
    if name in ("catalogue", "ingest_file", "read_manifest"):
        from ingest import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
