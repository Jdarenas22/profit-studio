"""Referencias USDA reproducibles y auditoría sin inferir nutrientes faltantes."""

from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from apps.plans.foods_data import food_rows
from .fdc import FDCError
from .fdc_normalize import normalize_food, OPTIONAL_FIELDS
from .preparation import validate_preparation

DATA_PATH = Path(__file__).resolve().parent.parent / "data/usda_reference.json"
FIELDS = ("kcal100", "protein100", "carbs100", "fat100", *OPTIONAL_FIELDS)


def _load_reference():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    seeds = {row["name"]: row for row in food_rows()}
    if data.get("format") != "profit-usda-reference/1" or not isinstance(
        data.get("foods"), list
    ):
        raise FDCError("fdc_catalog_invalid")
    names = [entry.get("name") for entry in data["foods"]]
    if len(names) != len(set(names)) or set(names) != set(seeds):
        raise FDCError("fdc_catalog_identity_set_mismatch")
    reviewed, pending = [], []
    for entry in data["foods"]:
        if entry.get("status") == "needs_specification":
            if not entry.get("reason") or entry.get("record") is not None:
                raise FDCError("fdc_catalog_invalid_pending")
            pending.append(entry)
            continue
        if entry.get("status") != "reviewed_reference":
            raise FDCError("fdc_catalog_invalid_status")
        if (
            not isinstance(entry.get("qualifier_es"), str)
            or not 1 <= len(entry["qualifier_es"]) <= 200
        ):
            raise FDCError("fdc_catalog_qualifier_required")
        values, snapshot, digest = normalize_food(entry["record"], entry["fdc_id"])
        if digest != entry.get("source_hash") or snapshot["description"] != entry.get(
            "expected_description"
        ):
            raise FDCError("fdc_catalog_hash_mismatch")
        validate_preparation(snapshot["description"], entry["state"], entry["name"])
        reviewed.append(
            {
                **entry,
                "values": values,
                "snapshot": snapshot,
                "seed": seeds[entry["name"]],
            }
        )
    return data, reviewed, pending


def load_reference():
    try:
        return _load_reference()
    except FDCError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise FDCError("fdc_catalog_invalid") from None


@lru_cache(maxsize=1)
def _reviewed_bindings():
    _, entries, _ = load_reference()
    return {
        entry["name"]: (entry["fdc_id"], entry["state"], entry["source_hash"])
        for entry in entries
    }


def reviewed_reference_matches(food):
    """El nombre local, el estado y la versión de composición corresponden al perfil curado."""
    try:
        return _reviewed_bindings().get(food.name) == (
            food.fdc_id,
            food.preparation_state,
            food.source_hash,
        )
    except FDCError:
        return False


@lru_cache(maxsize=1)
def _qualifiers():
    _, entries, _ = load_reference()
    return {entry["name"]: entry["qualifier_es"] for entry in entries}


def reference_qualifier(food):
    return _qualifiers().get(food.name, "") if reviewed_reference_matches(food) else ""


def source_is_consistent(food):
    """Un id o la etiqueta 'USDA' solos no demuestran la composición almacenada."""
    snapshot = food.source_snapshot
    if not isinstance(snapshot, dict) or not food.fdc_id or not food.source_hash:
        return False
    if (
        snapshot.get("provider") != "USDA FoodData Central"
        or snapshot.get("basis") != "100g"
        or snapshot.get("fdc_id") != food.fdc_id
    ):
        return False
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if digest != food.source_hash or food.preparation_state not in (
        "raw",
        "cooked",
        "as_sold",
        "prepared",
    ):
        return False
    try:
        validate_preparation(
            snapshot.get("description"), food.preparation_state, food.name
        )
        values = snapshot.get("nutrients", {})
        for field in FIELDS:
            expected = values.get(field)
            if expected is None:
                if getattr(food, field) is not None or field not in OPTIONAL_FIELDS:
                    return False
            else:
                value = Decimal(str(expected))
                if not value.is_finite() or value != getattr(food, field):
                    return False
    except (FDCError, InvalidOperation, ValueError, TypeError, AttributeError):
        return False
    return True


def reference_report():
    data, reviewed, pending = load_reference()
    results = []
    for entry in reviewed:
        values = entry["values"]
        results.append(
            {
                "name": entry["name"],
                "status": entry["status"],
                "fdc_id": entry["fdc_id"],
                "description": entry["expected_description"],
                "qualifier_es": entry["qualifier_es"],
                "state": entry["state"],
                "api_endpoint": f"https://api.nal.usda.gov/fdc/v1/food/{entry['fdc_id']}",
                "missing_nutrients": [
                    field for field in OPTIONAL_FIELDS if values[field] is None
                ],
                "changes_from_approximate_seed": {
                    field: {
                        "seed": (
                            str(entry["seed"].get(field))
                            if entry["seed"].get(field) is not None
                            else None
                        ),
                        "usda": (
                            str(values[field]) if values[field] is not None else None
                        ),
                    }
                    for field in FIELDS
                    if entry["seed"].get(field) != values[field]
                },
                "household_measure": "grams_only_until_separately_verified",
            }
        )
    results.extend(
        {"name": entry["name"], "status": entry["status"], "reason": entry["reason"]}
        for entry in pending
    )
    return {
        "format": "profit-food-audit/1",
        "retrieved_on": data["retrieved_on"],
        "total": len(results),
        "reviewed_references": len(reviewed),
        "needs_specification": len(pending),
        "scope": "Generic reference profiles; not a universal brand, recipe or professional validation.",
        "foods": results,
    }
