"""Reading converted logs. No model call, no exec, no openpyxl.

Conversion happens once per workbook (logs/convert.py). Everything here reads
what that produced: parquet for the data, manifest.jsonl for the catalogue.

    from logs import catalogue, load_log

    catalogue()        -> descriptions of every converted sheet, for context
    load_log("Jan.")   -> the DataFrame
"""

from pathlib import Path

import pandas as pd

from logs.schema import SheetEntry

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "data" / "log_index"
PARQUET_DIR = INDEX_DIR / "parquet"
MANIFEST = INDEX_DIR / "manifest.jsonl"

# Joins the levels of a multi-row header into one column name. ASCII on
# purpose: this lands in generated scripts and on a Windows console, where a
# middle dot renders as a question mark.
SEP = " | "


def read_manifest():
    """Every manifest entry; the newest write per (file_hash, sheet) wins."""
    if not MANIFEST.exists():
        return []
    entries = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = SheetEntry.model_validate_json(line)
        except Exception:  # noqa: BLE001 - one bad line must not blind the rest
            continue
        entries[(entry.file_hash, entry.sheet_name)] = entry
    return list(entries.values())


def append_manifest(entry):
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(entry.model_dump_json() + "\n")


def parquet_path(file_hash, sheet_name):
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in sheet_name)
    return PARQUET_DIR / f"{file_hash}__{safe.strip('_')}.parquet"


def find(sheet_name, file_hash=None):
    """The manifest entry for a sheet. Matches on name, exactly then loosely."""
    entries = [e for e in read_manifest() if e.status == "converted"]
    if file_hash:
        entries = [e for e in entries if e.file_hash == file_hash]
    for e in entries:
        if e.sheet_name == sheet_name:
            return e
    lowered = sheet_name.strip().lower()
    for e in entries:
        if e.sheet_name.strip().lower() == lowered:
            return e
    return None


def load_log(sheet_name, file_hash=None):
    """One converted sheet as a DataFrame. Reads parquet; nothing is executed.

    Exposed inside the run_python sandbox. Generated scripts call this rather
    than reading the .xlsx, so two runs of the same question see the same
    columns.
    """
    entry = find(sheet_name, file_hash)
    if entry is None:
        known = sorted(
            {e.sheet_name for e in read_manifest() if e.status == "converted"}
        )
        raise LookupError(
            f"no converted sheet named {sheet_name!r}. Available: "
            f"{', '.join(known) if known else '(none - run logs.convert first)'}"
        )
    return pd.read_parquet(entry.parquet_path)


def catalogue(include_unconverted=False):
    """Every converted sheet, described, for dropping into context.

    This is the whole of log retrieval. At this corpus size there is nothing to
    search: the model reads the catalogue and picks a sheet by name.
    """
    entries = sorted(read_manifest(), key=lambda e: (e.file_path, e.sheet_name))
    blocks = []
    for e in entries:
        if e.status == "converted":
            blocks.append(e.catalogue_entry())
        elif include_unconverted:
            blocks.append(
                f"sheet: {e.sheet_name}\nsource: {e.file_path}\n"
                f"NOT CONVERTED: {e.error}"
            )
    if not blocks:
        return "No operator log sheets have been converted yet."
    return "\n\n---\n\n".join(blocks)


def unit_of(name):
    """Unit parsed off a header label, e.g. 'Turbidity (NTU)' -> 'NTU'."""
    import re

    m = re.search(r"\(([^()]*)\)\s*$", str(name or ""))
    return m.group(1) if m else None


def describe_frame(df):
    """The factual fields, computed by pandas. The model states none of these."""
    columns = []
    for c in df.columns:
        s = df[c]
        numeric = pd.api.types.is_numeric_dtype(s)
        columns.append(
            dict(
                name=str(c),
                dtype=str(s.dtype),
                unit=unit_of(c),
                null_frac=round(float(s.isna().mean()), 3),
                min=(float(s.min()) if numeric and s.notna().any() else None),
                max=(float(s.max()) if numeric and s.notna().any() else None),
            )
        )

    # Low-cardinality text columns only: that is the vocabulary a filter needs
    # ("Y"/"N", operator initials). The distinct values of a turbidity column
    # are data, not vocabulary.
    distinct = {
        str(c): sorted({str(v) for v in df[c].dropna().unique()})
        for c in df.columns
        if not pd.api.types.is_numeric_dtype(df[c]) and 0 < df[c].nunique() <= 50
    }

    start = end = interval = None
    if isinstance(df.index, pd.DatetimeIndex) and df.index.notna().any():
        start, end = df.index.min(), df.index.max()
        if len(df) > 1:
            gaps = pd.Series(df.index.dropna()).sort_values().diff().dropna()
            if not gaps.empty:
                interval = {
                    pd.Timedelta(days=1): "daily",
                    pd.Timedelta(days=7): "weekly",
                }.get(gaps.median()) or f"median gap {gaps.median()}"

    return dict(
        row_count=len(df),
        columns=columns,
        coverage_start=start,
        coverage_end=end,
        sampling_interval=interval,
        distinct_values=distinct,
    )


def dump_catalogue(path=None):
    """Write the catalogue out so a human can read exactly what the model sees."""
    text = catalogue(include_unconverted=True)
    if path:
        Path(path).write_text(text, encoding="utf-8")
    return text
