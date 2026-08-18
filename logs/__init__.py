"""Operator log spreadsheets — converted once, read as parquet.

One LLM call per workbook writes a converter (logs/convert.py); code runs it
and writes one parquet file per sheet plus a manifest row. The query path
(logs/loader.py) reads parquet and never calls a model or executes code.

    from logs import catalogue, load_log

    catalogue()        -> every converted sheet, described, for context
    load_log("Jan.")   -> the DataFrame
"""

from logs.loader import catalogue, dump_catalogue, find, load_log, read_manifest
from logs.schema import ColumnInfo, SheetEntry, validate_frame

__all__ = [
    "ColumnInfo",
    "SheetEntry",
    "catalogue",
    "dump_catalogue",
    "find",
    "load_log",
    "read_manifest",
    "validate_frame",
]
