"""Stamps every action with code/config provenance for retroactive analysis."""

from __future__ import annotations

import hashlib
import os
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def config_version() -> str:
    sha = os.environ.get("FLY_IMAGE_REF") or os.environ.get("GIT_SHA")
    if sha:
        return sha[:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"


@lru_cache(maxsize=1)
def weights_version(config_yaml_path: str | Path = "config.yaml") -> str:
    """Hash of just the ranker_weights + ranker_caps sections so we can detect tuning changes."""
    import yaml

    path = Path(config_yaml_path)
    if not path.exists():
        return "unknown"
    data = yaml.safe_load(path.read_text())
    salient = {
        "ranker_weights": data.get("ranker_weights", {}),
        "ranker_caps": data.get("ranker_caps", {}),
        "saturation": data.get("saturation", {}),
    }
    payload = repr(sorted(salient.items())).encode()
    return hashlib.sha256(payload).hexdigest()[:12]
