"""Validate schema closure and the published immutable generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.integrity import verify_manifest


def main() -> None:
    schemas = list(Path("schemas/v3.0").glob("*.json"))
    if not schemas:
        raise SystemExit("no analysis schemas")
    for path in schemas:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            raise SystemExit(f"root contract is not closed: {path}")
    verify_manifest(Path("docs/manifest.json"))
    print(f"validated {len(schemas)} analysis schemas and immutable publication")


if __name__ == "__main__":
    main()
