"""Detecta contradicciones explícitas; no deduce identidad de una coincidencia de texto."""

import re
from .fdc import FDCError


def stated_preparation(description):
    text = (description or "").casefold()
    raw = bool(re.search(r"\b(raw|uncooked|crud[oa]s?)\b", text))
    cooked = bool(
        re.search(
            r"\b(cooked|boiled|roasted|fried|baked|stewed|grilled|cocid[oa]s?|asad[oa]s?|hervid[oa]s?|tostad[oa]s?)\b",
            text,
        )
    )
    if raw and cooked:
        return "ambiguous"
    return "raw" if raw else "cooked" if cooked else None


def validate_preparation(description, state, local_name=""):
    if state not in ("raw", "cooked", "as_sold", "prepared"):
        raise FDCError("fdc_preparation_state_required")
    source = stated_preparation(description)
    local = stated_preparation(local_name)
    if source == "ambiguous":
        raise FDCError("fdc_preparation_ambiguous")
    if source in ("raw", "cooked") and state in ("raw", "cooked") and source != state:
        raise FDCError("fdc_source_preparation_mismatch")
    if local in ("raw", "cooked") and state in ("raw", "cooked") and local != state:
        raise FDCError("fdc_local_preparation_mismatch")
    if local in ("raw", "cooked") and source in ("raw", "cooked") and local != source:
        raise FDCError("fdc_local_preparation_mismatch")
