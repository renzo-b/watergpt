"""What we record about one converted sheet.

One LLM call per workbook writes a converter; code runs it, writes a parquet
file per sheet, and appends a manifest entry here. Nothing in this file is
executed at query time — the query path reads parquet.

The manifest is the catalogue. Descriptions from it go into context so the
model can pick a sheet by reading, which at this corpus size (17 sheets) beats
any retrieval index.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    """One column of the converted frame. Computed by pandas, never by the model."""

    name: str
    dtype: str
    unit: str | None = None  # parsed off the header text, e.g. "NTU"
    null_frac: float
    min: float | str | None = None
    max: float | str | None = None


class SheetEntry(BaseModel):
    """One manifest row: one sheet, one parquet file, one description."""

    # ---- provenance: what this parquet came from ----
    file_path: str
    file_hash: str
    sheet_name: str
    parquet_path: str | None = None
    generated_at: datetime

    status: Literal["converted", "unconverted"] = "converted"
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)

    # ---- from the model ----
    description: str = ""
    column_gloss: dict[str, str] = Field(default_factory=dict)

    # ---- computed from the converted frame ----
    row_count: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    sampling_interval: str | None = None
    distinct_values: dict[str, list[str]] = Field(default_factory=dict)

    # Cells landed in the parquet over cells populated in the source sheet.
    # The lossy-conversion signal: a converter that read the wrong region, or
    # dropped columns it did not understand, shows up here as a low number
    # rather than as a surprise months later. Recorded, never enforced —
    # a sheet that is mostly formatting legitimately scores low.
    cell_coverage: float | None = None

    # The converter that produced this, kept for traceability: it answers
    # "how was this parquet made" without re-running anything. Shared by every
    # sheet from the same workbook and the same call.
    converter_source: str = ""

    def catalogue_entry(self):
        """The compact form that goes into context. Not the whole entry."""
        lines = [
            f"sheet: {self.sheet_name}",
            f"source: {self.file_path}",
            f"description: {self.description}",
        ]
        if self.coverage_start and self.coverage_end:
            lines.append(
                f"coverage: {self.coverage_start:%Y-%m-%d} to "
                f"{self.coverage_end:%Y-%m-%d}"
                + (f" ({self.sampling_interval})" if self.sampling_interval else "")
            )
        lines.append(f"rows: {self.row_count}, columns: {len(self.columns)}")
        if self.column_gloss:
            lines.append("columns mean:")
            lines += [f"  {k}: {v}" for k, v in self.column_gloss.items()]
        if self.distinct_values:
            lines.append("filter values:")
            lines += [
                f"  {k}: {', '.join(v[:12])}" for k, v in self.distinct_values.items()
            ]
        if self.warnings:
            lines.append("caveats: " + "; ".join(self.warnings))
        return "\n".join(lines)


def validate_frame(df):
    """Checks a converted frame must pass before its parquet is kept.

    Returns (failures, warnings). A failure means the conversion is wrong and
    the sheet is marked unconverted. A warning means the conversion is right
    and the source data is odd — recorded on the entry, never a rejection.

    Deliberately thin. These catch a converter that produced nothing usable;
    they cannot catch a converter that read the wrong region and produced
    something plausible. That is what cell_coverage and an openable parquet
    file are for — the file is the check a human can actually make.
    """
    failures = []
    warnings = []

    names = [str(c) for c in df.columns]
    if any(n.strip() == "" for n in names):
        failures.append("empty column name")
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        failures.append(f"duplicate column names: {sorted(dupes)[:5]}")
    if len(df) == 0:
        failures.append("no rows")
        return failures, warnings
    if not len(df.columns):
        failures.append("no columns")
        return failures, warnings

    import pandas as pd

    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.isna().mean() > 0.10:
            failures.append(f"{df.index.isna().mean():.0%} of dates failed to parse")
        if df.index.has_duplicates:
            dupes = sorted({f"{d:%Y-%m-%d}" for d in df.index[df.index.duplicated()]})
            warnings.append(f"duplicate dates: {dupes[:5]}")

    return failures, warnings
