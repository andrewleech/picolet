"""config_validator.py — minimal JSON Schema Draft-07 subset validator.

Implements the subset of JSON Schema relevant to config files:
    type, required, properties, additionalProperties,
    minimum, maximum, exclusiveMinimum, exclusiveMaximum,
    minLength, maxLength, pattern, enum, items.

Returns a list of error dicts: [{"path": "a.b", "message": "..."}]
An empty list means no errors.

Licence: MIT (Picolet project).
"""
from __future__ import annotations
import re as _re


def validate(document, schema: dict) -> list:
    """Validate document against schema. Returns list of error dicts."""
    errors: list = []
    _validate_node(document, schema, "", errors)
    return errors


def _validate_node(value, schema: dict, path: str, errors: list) -> None:
    if not isinstance(schema, dict):
        return

    # Type check.
    t = schema.get("type")
    if t is not None:
        if not _check_type(value, t):
            errors.append({
                "path": path,
                "message": f"expected type {t!r}, got {type(value).__name__}",
            })
            return  # Further checks not meaningful if type is wrong.

    # Enum check.
    if "enum" in schema and value not in schema["enum"]:
        errors.append({
            "path": path,
            "message": f"value {value!r} not in enum {schema['enum']!r}",
        })

    # Type-specific checks.
    if isinstance(value, dict):
        _validate_object(value, schema, path, errors)
    elif isinstance(value, list):
        _validate_array(value, schema, path, errors)
    elif isinstance(value, str):
        _validate_string(value, schema, path, errors)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path, errors)


def _check_type(value, t: str) -> bool:
    """Return True if value matches the JSON Schema type string t."""
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return True  # Unknown type — pass through.


def _validate_object(value: dict, schema: dict, path: str, errors: list) -> None:
    # required
    for req in schema.get("required", []):
        if req not in value:
            p = f"{path}.{req}" if path else req
            errors.append({"path": p, "message": f"required key {req!r} is missing"})

    # properties
    props = schema.get("properties", {})
    for k, v in value.items():
        p = f"{path}.{k}" if path else k
        if k in props:
            _validate_node(v, props[k], p, errors)
        else:
            # additionalProperties
            add = schema.get("additionalProperties")
            if add is False:
                errors.append({"path": p, "message": f"additional property {k!r} is not allowed"})
            elif isinstance(add, dict):
                _validate_node(v, add, p, errors)


def _validate_array(value: list, schema: dict, path: str, errors: list) -> None:
    items_schema = schema.get("items")
    if items_schema is not None:
        for i, item in enumerate(value):
            p = f"{path}[{i}]"
            _validate_node(item, items_schema, p, errors)

    min_items = schema.get("minItems")
    if min_items is not None and len(value) < min_items:
        errors.append({"path": path, "message": f"array has {len(value)} items, minimum is {min_items}"})

    max_items = schema.get("maxItems")
    if max_items is not None and len(value) > max_items:
        errors.append({"path": path, "message": f"array has {len(value)} items, maximum is {max_items}"})


def _validate_string(value: str, schema: dict, path: str, errors: list) -> None:
    min_len = schema.get("minLength")
    if min_len is not None and len(value) < min_len:
        errors.append({"path": path, "message": f"string length {len(value)} is less than minLength {min_len}"})

    max_len = schema.get("maxLength")
    if max_len is not None and len(value) > max_len:
        errors.append({"path": path, "message": f"string length {len(value)} exceeds maxLength {max_len}"})

    pattern = schema.get("pattern")
    if pattern is not None:
        if not _re.search(pattern, value):
            errors.append({"path": path, "message": f"string does not match pattern {pattern!r}"})


def _validate_number(value, schema: dict, path: str, errors: list) -> None:
    minimum = schema.get("minimum")
    if minimum is not None and value < minimum:
        errors.append({"path": path, "message": f"value {value} is less than minimum {minimum}"})

    maximum = schema.get("maximum")
    if maximum is not None and value > maximum:
        errors.append({"path": path, "message": f"value {value} exceeds maximum {maximum}"})

    exc_min = schema.get("exclusiveMinimum")
    if exc_min is not None and value <= exc_min:
        errors.append({"path": path, "message": f"value {value} is not greater than exclusiveMinimum {exc_min}"})

    exc_max = schema.get("exclusiveMaximum")
    if exc_max is not None and value >= exc_max:
        errors.append({"path": path, "message": f"value {value} is not less than exclusiveMaximum {exc_max}"})

    multiple_of = schema.get("multipleOf")
    if multiple_of is not None and multiple_of != 0:
        if value % multiple_of != 0:
            errors.append({"path": path, "message": f"value {value} is not a multiple of {multiple_of}"})
