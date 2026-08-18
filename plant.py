"""Where a plant's data lives. One place, so nothing has to agree by accident.

Everything a plant knows sits under one directory:

    data/plants/<plant-id>/
        documents.jsonl     one DocEntry per line   - ingest/
        logs.jsonl          one SheetEntry per line - logs/
        parquet/            converted log sheets
        parsed/             cached docling parses

The layout used to be one folder per script - context_index, log_index,
parsed, interp - which organised the data by which program wrote it rather
than by what it is. This organises it by the only distinction that matters
operationally: everything here belongs to one plant, so it can be backed up,
copied or handed over as a unit, and two customers who both upload
"Daily Log.xlsx" cannot collide.

`parsed/` is the one subdirectory that is safe to delete: it is a cache of
docling output, expensive to rebuild (minutes for a scanned manual) but
rebuildable from the source files with no model call. Everything else was paid
for with API calls and cannot be recovered by re-running anything.

Paths recorded INSIDE a manifest are stored relative to the repository root,
never absolute. An absolute path bakes one machine's home directory into a
file that outlives it, and quietly breaks the moment the directory moves - the
manifest keeps pointing somewhere that no longer exists and nothing notices
until a query fails.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULT_PLANT = "demo"


def plant_dir(plant=None):
    return ROOT / "data" / "plants" / (plant or DEFAULT_PLANT)


def documents_manifest(plant=None):
    """The document catalogue written by ingest/."""
    return plant_dir(plant) / "documents.jsonl"


def logs_manifest(plant=None):
    """The converted-sheet catalogue written by logs/."""
    return plant_dir(plant) / "logs.jsonl"


def parquet_dir(plant=None):
    return plant_dir(plant) / "parquet"


def parsed_dir(plant=None):
    """Cached docling parses. Regenerable; safe to delete to reclaim disk."""
    return plant_dir(plant) / "parsed"


def scratch_dir():
    """Where debug output goes: dry-run payloads, catalogue dumps.

    Deliberately outside the plant directory. Everything under data/plants is
    a plant's knowledge and should survive being copied or handed to a
    customer; a dump of what would have been sent to a model is neither, and
    mixing the two makes the plant folder impossible to hand over without
    reading it first. gitignored, and safe to empty at any time.
    """
    return ROOT / "scratch"


def relative(path):
    """A path as recorded in a manifest: relative to the repo root, POSIX."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        # Outside the repo. Nothing should write one, but recording the
        # absolute path is better than raising during an ingest that has
        # already been paid for.
        return path.as_posix()


def resolve(recorded):
    """A path read back from a manifest, made absolute again.

    Tolerates the absolute paths written before this module existed, so an
    older manifest keeps working rather than failing to load.
    """
    path = Path(recorded)
    return path if path.is_absolute() else ROOT / path
