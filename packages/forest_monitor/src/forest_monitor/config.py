"""Project configuration and path helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _find_project_root() -> Path:
    """Locate the monorepo root without depending on the caller's directory."""
    configured = os.getenv("FOREST_MONITOR_ROOT")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend((Path.cwd(), Path(__file__).resolve().parent))

    visited: set[Path] = set()
    for start in candidates:
        for candidate in (start, *start.parents):
            candidate = candidate.resolve()
            if candidate in visited:
                continue
            visited.add(candidate)
            if (candidate / "configs" / "config.yaml").is_file() and (
                candidate / "ROADMAP.md"
            ).is_file():
                return candidate
    raise RuntimeError(
        "Could not locate the nigeria-forest-monitor root. "
        "Set FOREST_MONITOR_ROOT to the repository directory."
    )


PROJECT_ROOT = _find_project_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.yaml"


class ConfigError(ValueError):
    """Raised when the project configuration is incomplete or inconsistent."""


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return DEFAULT_CONFIG_PATH
    path = Path(config_path)
    if path.is_absolute():
        return path
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate.resolve()
    return (PROJECT_ROOT / path).resolve()


def load_config(config_path: str | Path | None = None, *, validate: bool = True) -> dict[str, Any]:
    """Load config independently of the caller's current working directory."""
    path = _resolve_config_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if validate:
        validate_config(config)
    config["_meta"] = {"config_path": str(path), "project_root": str(PROJECT_ROOT)}
    return config


def validate_config(config: dict[str, Any]) -> None:
    required = {
        "project",
        "aoi",
        "sentinel1",
        "grid",
        "change_detection",
        "classifier",
        "risk",
        "paths",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise ConfigError(f"Missing config sections: {', '.join(missing)}")

    bbox = config["aoi"].get("bbox", [])
    if len(bbox) != 4 or not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        raise ConfigError("aoi.bbox must be [lon_min, lat_min, lon_max, lat_max]")

    weights = config["risk"].get("weights", {})
    expected_weights = {"change_detection", "classifier", "acled_proximity"}
    if set(weights) != expected_weights:
        raise ConfigError(f"risk.weights must contain exactly {sorted(expected_weights)}")
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-6:
        raise ConfigError("risk.weights must sum to 1.0")

    splits = config["classifier"]
    split_total = sum(
        float(splits.get(key, 0.0)) for key in ("train_split", "val_split", "test_split")
    )
    if abs(split_total - 1.0) > 1e-6:
        raise ConfigError("classifier train/val/test splits must sum to 1.0")

    resolution = float(config["sentinel1"].get("resolution_m", 0))
    sampling_scale = float(config["classifier"].get("sampling_scale_m", resolution))
    if resolution <= 0 or sampling_scale < resolution:
        raise ConfigError("classifier.sampling_scale_m must be >= sentinel1.resolution_m")


def resolve_path(config: dict[str, Any], key: str, *, create: bool = False) -> Path:
    """Resolve a configured project path and optionally create the directory."""
    try:
        raw_path = config["paths"][key]
    except KeyError as exc:
        raise ConfigError(f"Unknown configured path: {key}") from exc
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_project_directories(config: dict[str, Any]) -> None:
    for key in config["paths"]:
        resolve_path(config, key, create=True)
