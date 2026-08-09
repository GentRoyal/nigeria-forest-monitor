"""Memory-safe SAR feature extraction and classification.

The classifier intentionally consumes compact neighbourhood statistics instead
of downloading image patches.  Earth Engine computes the statistics and only a
small bounded table crosses the network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import ee
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from loguru import logger
from torch.utils.data import DataLoader, Dataset

from forest_monitor.config import PROJECT_ROOT, load_config

CLASS_NAMES = {0: "normal_forest", 1: "clearing", 2: "structure", 3: "path_or_track"}
SUSPICIOUS_CLASSES = {1, 2, 3}
FEATURE_NAMES = ("VV", "VH", "VV_mean", "VH_mean", "VV_std", "VH_std")
N_FEATURES = len(FEATURE_NAMES)


class SARFeatureMLP(nn.Module):
    """Small MLP for six SAR neighbourhood features."""

    def __init__(self, num_classes: int = 4, n_features: int = N_FEATURES):
        super().__init__()
        self.n_features = n_features
        self.register_buffer("feature_mean", torch.zeros(n_features))
        self.register_buffer("feature_scale", torch.ones(n_features))
        self.classifier = nn.Sequential(
            nn.Linear(n_features, 64),
            nn.LayerNorm(64),
            nn.ReLU(),
            nn.Dropout(0.25),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(64, num_classes),
        )

    def set_scaler(self, features: np.ndarray) -> None:
        mean = np.nanmean(features, axis=0).astype(np.float32)
        scale = np.nanstd(features, axis=0).astype(np.float32)
        scale[scale < 1e-6] = 1.0
        self.feature_mean.copy_(torch.from_numpy(mean))
        self.feature_scale.copy_(torch.from_numpy(scale))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = (x - self.feature_mean) / self.feature_scale
        return self.classifier(x)


# Backward-compatible name used by existing notebooks and checkpoints.
SARPatchCNN = SARFeatureMLP


class SARFeatureDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray, augment: bool = False):
        self.features = torch.as_tensor(features, dtype=torch.float32)
        self.labels = torch.as_tensor(labels, dtype=torch.long)
        self.augment = augment

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, index: int):
        feature = self.features[index]
        if self.augment:
            feature = feature + torch.randn_like(feature) * 0.01
        return feature, self.labels[index]


# Backward-compatible dataset name.
SARPatchDataset = SARFeatureDataset


def _feature_image(image: ee.Image, radius: int) -> ee.Image:
    base = image.select(["VV", "VH"])
    kernel = ee.Kernel.square(radius=radius, units="pixels")
    means = base.reduceNeighborhood(ee.Reducer.mean(), kernel).rename(["VV_mean", "VH_mean"])
    stds = base.reduceNeighborhood(ee.Reducer.stdDev(), kernel).rename(["VV_std", "VH_std"])
    return base.addBands(means).addBands(stds).select(list(FEATURE_NAMES))


def _is_capacity_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(token in message for token in ("too large", "memory limit", "timed out", "capacity"))


def extract_patches_from_gee(
    image: ee.Image,
    change_mask: ee.Image,
    aoi: ee.Geometry,
    config: dict,
    n_patches: int | None = None,
) -> np.ndarray:
    """Extract a bounded table of SAR features from changed pixels.

    Sampling starts at ``classifier.sampling_scale_m`` and automatically retries
    at coarser scales for Earth Engine capacity failures.  No collection size
    reduction is performed, avoiding the full-resolution count that caused the
    recurring 80 MiB notebook error.
    """
    classifier_cfg = config["classifier"]
    native_scale = int(config["sentinel1"]["resolution_m"])
    start_scale = max(int(classifier_cfg.get("sampling_scale_m", 100)), native_scale)
    radius = max(1, int(classifier_cfg.get("neighborhood_radius_px", 3)))
    requested = int(n_patches or classifier_cfg.get("n_samples", 200))
    seed = int(classifier_cfg.get("seed", 42))
    if requested <= 0:
        raise ValueError("n_patches must be greater than zero")

    base_features = _feature_image(image, radius)
    native_projection = image.select("VV").projection()
    scales = tuple(dict.fromkeys((start_scale, start_scale * 2, start_scale * 4)))
    last_error: Exception | None = None

    for scale in scales:
        try:
            logger.info(f"Sampling up to {requested} changed locations at {scale} m...")
            if scale > native_scale:
                sampling_mask = change_mask.reduceResolution(
                    reducer=ee.Reducer.max(), bestEffort=True, maxPixels=1024
                ).reproject(crs=native_projection, scale=scale)
            else:
                sampling_mask = change_mask
            features_image = base_features.addBands(
                sampling_mask.gt(0).rename("change_class")
            ).clip(aoi)
            collection = features_image.stratifiedSample(
                numPoints=0,
                classBand="change_class",
                region=aoi,
                scale=scale,
                classValues=[1],
                classPoints=[requested],
                seed=seed,
                dropNulls=True,
                tileScale=16,
                geometries=False,
            )
            payload = collection.getInfo()
            items = payload.get("features", []) if isinstance(payload, dict) else []
            rows = []
            for item in items:
                properties = item.get("properties", {})
                if properties.get("change_class") == 1 and all(
                    properties.get(name) is not None for name in FEATURE_NAMES
                ):
                    rows.append([float(properties[name]) for name in FEATURE_NAMES])
            if not rows:
                logger.warning("No changed pixels produced complete SAR features")
                return np.empty((0, N_FEATURES), dtype=np.float32)
            features = np.asarray(rows, dtype=np.float32)
            logger.success(f"Extracted {len(features)} feature vectors at {scale} m")
            return features
        except ee.EEException as error:
            last_error = error
            if not _is_capacity_error(error) or scale == scales[-1]:
                raise
            logger.warning(f"Earth Engine capacity limit at {scale} m; retrying coarser")

    if last_error is not None:
        raise last_error
    return np.empty((0, N_FEATURES), dtype=np.float32)


def generate_pseudo_labels(
    features: np.ndarray,
    change_scores: np.ndarray | None = None,
) -> np.ndarray:
    """Generate deterministic weak labels for demonstrations only.

    Production training should use reviewed labels.  The heuristic uses local
    backscatter contrast and texture; an optional real change magnitude can
    influence the clearing class.  Random scores should not be supplied.
    """
    features = _validate_features(features, allow_empty=True)
    if len(features) == 0:
        return np.empty(0, dtype=np.int64)

    vv, vh, vv_mean, vh_mean, vv_std, vh_std = features.T
    contrast = np.abs(vv - vv_mean) + np.abs(vh - vh_mean)
    texture = vv_std + vh_std
    contrast_hi = np.quantile(contrast, 0.70)
    texture_hi = np.quantile(texture, 0.70)
    texture_lo = np.quantile(texture, 0.30)

    labels = np.zeros(len(features), dtype=np.int64)
    labels[(contrast >= contrast_hi) & (texture < texture_hi)] = 1
    labels[texture >= texture_hi] = 2
    labels[(contrast >= np.median(contrast)) & (texture <= texture_lo)] = 3

    if change_scores is not None:
        scores = np.asarray(change_scores, dtype=float)
        if scores.shape != (len(features),):
            raise ValueError("change_scores must contain one value per feature row")
        labels[(scores >= 0.8) & (labels == 0)] = 1

    counts = {CLASS_NAMES[index]: int((labels == index).sum()) for index in CLASS_NAMES}
    logger.info(f"Weak-label distribution: {counts}")
    return labels


def _validate_features(features: np.ndarray, *, allow_empty: bool = False) -> np.ndarray:
    array = np.asarray(features, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != N_FEATURES:
        raise ValueError(f"features must have shape (N, {N_FEATURES}); got {array.shape}")
    if not allow_empty and len(array) == 0:
        raise ValueError("features cannot be empty")
    if not np.isfinite(array).all():
        raise ValueError("features contain NaN or infinite values")
    return array


def train_classifier(
    patches: np.ndarray,
    labels: np.ndarray,
    config: dict,
    save_path: str | Path | None = None,
) -> SARFeatureMLP:
    features = _validate_features(patches)
    labels = np.asarray(labels, dtype=np.int64)
    if labels.shape != (len(features),):
        raise ValueError("labels must contain one integer per feature row")
    if len(features) < 8:
        raise ValueError("At least 8 labelled samples are required for training")
    if labels.min() < 0 or labels.max() >= len(CLASS_NAMES):
        raise ValueError("labels must be integers from 0 to 3")

    cfg = config["classifier"]
    seed = int(cfg.get("seed", 42))
    np.random.seed(seed)
    torch.manual_seed(seed)
    indices = np.random.permutation(len(features))
    val_fraction = float(cfg.get("val_split", 0.15))
    val_count = max(1, int(round(len(features) * val_fraction)))
    train_indices, val_indices = indices[val_count:], indices[:val_count]
    if len(train_indices) < 2:
        raise ValueError("Training split is too small")

    model = SARFeatureMLP(num_classes=len(CLASS_NAMES))
    model.set_scaler(features[train_indices])
    train_loader = DataLoader(
        SARFeatureDataset(features[train_indices], labels[train_indices], augment=True),
        batch_size=min(int(cfg["batch_size"]), len(train_indices)),
        shuffle=True,
    )
    val_loader = DataLoader(
        SARFeatureDataset(features[val_indices], labels[val_indices]),
        batch_size=min(int(cfg["batch_size"]), len(val_indices)),
    )

    optimizer = optim.Adam(model.parameters(), lr=float(cfg["learning_rate"]))
    criterion = nn.CrossEntropyLoss()
    patience = int(cfg["early_stopping_patience"])
    best_loss = float("inf")
    best_state = None
    stale_epochs = 0

    for epoch in range(int(cfg["epochs"])):
        model.train()
        for batch_features, batch_labels in train_loader:
            optimizer.zero_grad()
            loss = criterion(model(batch_features), batch_labels)
            loss.backward()
            optimizer.step()

        model.eval()
        validation_loss = 0.0
        with torch.no_grad():
            for batch_features, batch_labels in val_loader:
                validation_loss += criterion(model(batch_features), batch_labels).item()
        validation_loss /= max(1, len(val_loader))

        if validation_loss < best_loss - 1e-6:
            best_loss = validation_loss
            best_state = {name: value.detach().clone() for name, value in model.state_dict().items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                logger.info(f"Early stopping at epoch {epoch + 1}")
                break

    if best_state is None:
        raise RuntimeError("Training did not produce a valid model state")
    model.load_state_dict(best_state)
    model.eval()
    logger.success(f"Training complete; best validation loss={best_loss:.4f}")
    if save_path is not None:
        save_model(model, save_path, config, val_loss=best_loss)
    return model


def classify_patches(model: SARFeatureMLP, features: np.ndarray, config: dict | None = None) -> dict:
    del config  # retained for notebook API compatibility
    array = _validate_features(features)
    model.eval()
    with torch.no_grad():
        probabilities = torch.softmax(model(torch.from_numpy(array)), dim=1).cpu().numpy()
    labels = probabilities.argmax(axis=1)
    suspicious = np.isin(labels, tuple(SUSPICIOUS_CLASSES))
    return {
        "labels": labels,
        "probs": probabilities,
        "suspicious": suspicious,
        "class_names": [CLASS_NAMES[int(label)] for label in labels],
    }


def extract_grid_features_from_gee(
    image: ee.Image,
    grid,
    config: dict,
):
    """Return ``(cell_ids, features)`` using one server-side grid reduction."""
    from forest_monitor.ingestion.grid import grid_to_ee_feature_collection

    if grid.empty:
        return np.empty(0, dtype=np.int64), np.empty((0, N_FEATURES), dtype=np.float32)
    cfg = config["classifier"]
    scale = max(int(cfg.get("sampling_scale_m", 100)), int(config["sentinel1"]["resolution_m"]))
    radius = max(1, int(cfg.get("neighborhood_radius_px", 3)))
    reduced = _feature_image(image, radius).reduceRegions(
        collection=grid_to_ee_feature_collection(grid),
        reducer=ee.Reducer.mean(),
        scale=scale,
        tileScale=8,
    ).getInfo()
    ids, rows = [], []
    for feature in reduced.get("features", []):
        props = feature.get("properties", {})
        if props.get("cell_id") is None or not all(props.get(name) is not None for name in FEATURE_NAMES):
            continue
        ids.append(int(props["cell_id"]))
        rows.append([float(props[name]) for name in FEATURE_NAMES])
    return np.asarray(ids, dtype=np.int64), np.asarray(rows, dtype=np.float32).reshape(-1, N_FEATURES)


def classify_grid_cells(model: SARFeatureMLP, image: ee.Image, grid, config: dict):
    """Return cell_id/classifier_score pairs suitable for risk fusion."""
    import pandas as pd

    cell_ids, features = extract_grid_features_from_gee(image, grid, config)
    if len(features) == 0:
        return pd.DataFrame({"cell_id": grid["cell_id"], "classifier_score": 0.0})
    probabilities = classify_patches(model, features)["probs"]
    scores = probabilities[:, 1:].max(axis=1)
    return pd.DataFrame({"cell_id": cell_ids, "classifier_score": scores})

def _resolve_model_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def save_model(
    model: SARFeatureMLP,
    path: str | Path,
    config: dict,
    val_loss: float | None = None,
) -> Path:
    output = _resolve_model_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state": model.state_dict(),
            "n_features": model.n_features,
            "feature_names": FEATURE_NAMES,
            "class_names": CLASS_NAMES,
            "val_loss": val_loss,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "classifier_config": config["classifier"],
        },
        output,
    )
    logger.success(f"Model saved -> {output}")
    return output


def load_model(path: str | Path) -> SARFeatureMLP:
    model_path = _resolve_model_path(path)
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    model = SARFeatureMLP(num_classes=len(CLASS_NAMES), n_features=int(checkpoint.get("n_features", N_FEATURES)))
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    logger.success(f"Model loaded from {model_path}")
    return model


__all__ = [
    "CLASS_NAMES", "FEATURE_NAMES", "N_FEATURES", "SARFeatureMLP", "SARPatchCNN",
    "extract_patches_from_gee", "generate_pseudo_labels", "train_classifier",
    "classify_patches", "extract_grid_features_from_gee", "classify_grid_cells",
    "save_model", "load_model", "load_config",
]