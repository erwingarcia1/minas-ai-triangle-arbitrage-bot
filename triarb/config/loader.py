"""YAML config loading.

Failure modes: a missing file, unparseable YAML, or a config that fails model
validation raises immediately with the offending path/field — never a partial
default silently substituted for a typo'd key (``extra="forbid"`` on every
model rejects unknown keys).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from triarb.config.models import Settings


def load_settings(path: str | Path) -> Settings:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"config root in {p} must be a mapping, got {type(raw).__name__}")
    return Settings.model_validate(raw)
