"""
Calculators — deterministic, testable, no LLM involved.

Every calculator in this package returns a dict of this shape. It is a
convention, not an enforced contract: nothing validates it, and tools outside
this package (lookups, retrievals) still return a plain string.

    {
      "summary":     str,   # operator-facing text; THE thing the model sees
      "result":      dict,  # headline numbers, keys specific to the calculator
      "steps":       list,  # [{label, formula, substituted, value, unit}, ...]
      "conversions": list,  # [str], unit conversions and ambiguity notes
      "caveats":     list,  # [str], may be empty
    }

Two rules that make the trace trustworthy:

  1. "summary" is the operator-facing string, unchanged. tools.registry.dispatch
     unwraps the dict to this string, so the agent loop and the eval harness see
     exactly what they saw before a trace was added. Build the dict around the
     string; never regenerate the string from the trace.

  2. Steps are captured from the intermediates as they are computed — never
     recalculated, and never produced or narrated by the model. Model-generated
     arithmetic is untrusted. A step entry may round its value for display; it
     may not derive it a second time.

"result" keys are deliberately per-calculator (calc_ct has ct_ratio/verdict,
calc_chemical_feed has feed rates), so a UI renders "summary", "steps",
"conversions" and "caveats" generically and "result" per tool.

See calc_ct.py for the reference implementation.
"""

from .calc_ct import *
from .calc_chemical_feed import *
from .calc_ph_adjustment import *
from .calc_surface_overflow_rate import *
from .calc_hydraulic_retention_time import *
from .calc_sludge_quantity import *
from .calc_food_to_microorganism_ratio import *
from .calc_srt import *
from .calc_solids_loading_rate import *
from .calc_svi import *
