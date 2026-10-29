"""Load the human-written schema documentation (docs/schema.yaml)."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SCHEMA_DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "schema.yaml"


@lru_cache
def load_schema_doc(path: Path = SCHEMA_DOC_PATH) -> dict[str, Any]:
    """Parse schema.yaml. Cached: the file is read once per process."""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
