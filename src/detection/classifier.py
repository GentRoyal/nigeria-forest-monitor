# ============================================================
# src/detection/classifier.py
#
# CNN patch classifier for SAR anomaly typing.
# Fully server-side patch extraction — no sampleRectangle.
#
# Classes:
#   0 = normal_forest
#   1 = clearing
#   2 = structure
#   3 = path_or_track
# ============================================================

import ee
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import yaml
import json
from pathlib import Path
from loguru import logger
from datetime import datetime


def load_config(config_path: str = "configs/config.yaml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


CLASS_NAMES = {
    0: "normal_forest",
    1: "clearing",
    2: "structure",
    3: "path_or_track"
}

SUSPICIOUS_CLASSES = {1, 2, 3}
PATCH_SIZE  = 16    # 16x16 neighbourhood — small, fast, memory-safe
SCALE_M     = 100   # 100m per pixel — coarse but avoids memory errors
N_FEATURES  = 6     # VV, VH, VV_mean, VH_mean, VV_std, VH_std


# ============================================================
# Model: Feature-based MLP
# (replaces CNN — works with neighbourhood statistics
#  instead of raw pixel arrays, fully memory-safe)
# ============================================================

class SARFeatureMLP(nn.Module):
    """
    MLP classifier on SAR neighbourhood statistics.
    Input:  (batch, N_FEATURES)  — per-point feature vector
    Output: (batch, 4)           — class logits

    Features per point:
        VV, VH                   — raw backscatter at point
        VV_mean, VH_mean         — mean in 16x16 neighbourhood
        VV_std,  VH_std          — std in 16x16 neighbourhood

    ~50k parameters — instant on CPU
    """
    def __init__(self, num_classes: int = 4, n_features: int = N_FEATURES):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(64, 128),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, num_classes)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ============================================================
# Dataset
# ============================================================

class SARFeatureDataset(Dataset):
    def __init__(self, features: np.ndarray, labels: np.ndarray):
        self.features = torch.tensor(features, dtype=torch.float32)
        self.labels   = torch.tensor(labels,   dtype=torch.long)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.features[idx], self.labels[idx]


# ============================================================
# Fully server-side feature extraction
# ============================================================

def extract_features_from_gee(
    image:       ee.Image,
    change_mask: ee.Image,
    aoi:         ee.Geometry,
    config:      dict,
    n_samples:   int = 300,
    include_normal: bool = True
) -> tuple:
    """
    Extract SAR features entirely server-side using GEE.
    No sampleRectangle — no memory errors.

    For each sampled point computes:
        VV, VH                  raw backscatter
        VV_mean, VH_mean        neighbourhood mean (16x16 @ 100m)
        VV_std,  VH_std         neighbourhood std

    Returns:
        features: np.ndarray (N, 6)
        is_changed: np.ndarray (N,) bool — True if from changed pixel
    """
    kernel = ee.Kernel.square(radius=PATCH_SIZE // 2, units="pixels")

    # Neighbourhood statistics — fully server-side
    vv_mean = image.select("VV").reduceNeighborhood(ee.Reducer.mean(),   kernel).rename("VV_mean")
    vh_mean = image.select("VH").reduceNeighborhood(ee.Reducer.mean(),   kernel).rename("VH_mean")
    vv_std  = image.select("VV").reduceNeighborhood(ee.Reducer.stdDev(), kernel).rename("VV_std")
    vh_std  = image.select("VH").reduceNeighborhood(ee.Reducer.stdDev(), kernel).rename("VH_std")

    feature_image = image.select(["VV", "VH"]).addBands([
        vv_mean, vh_mean, vv_std, vh_std
    ])

    all_features  = []
    all_changed   = []

    # --- Sample from CHANGED pixels ---
    changed_samples = (
        feature_image
        .updateMask(change_mask)
        .sample(
            region     = aoi,
            scale      = SCALE_M,
            numPixels  = n_samples,
            seed       = 42,
            tileScale  = 16,        # aggressive tiling — prevents memory errors
            geometries = False
        )
    )

    changed_count = changed_samples.size().getInfo()

    if changed_count > 0:
        changed_list = changed_samples.toList(changed_count).getInfo()
        for item in changed_list:
            props = item["properties"]
            row   = [
                props.get("VV",      -15.0),
                props.get("VH",      -20.0),
                props.get("VV_mean", -15.0),
                props.get("VH_mean", -20.0),
                props.get("VV_std",    2.0),
                props.get("VH_std",    2.0),
            ]
            all_features.append(row)
            all_changed.append(True)

        logger.info(f"Changed pixels sampled: {changed_count}")

    # --- Sample from NORMAL (unchanged) pixels for context ---
    if include_normal:
        normal_mask    = change_mask.Not()
        normal_samples = (
            feature_image
            .updateMask(normal_mask)
            .sample(
                region     = aoi,
                scale      = SCALE_M,
                numPixels  = n_samples // 2,
                seed       = 99,
                tileScale  = 16,
                geometries = False
            )
        )

        normal_count = normal_samples.size().getInfo()

        if normal_count > 0:
            normal_list = normal_samples.toList(normal_count).getInfo()
            for item in normal_list:
                props = item["properties"]
                row   = [
                    props.get("VV",      -15.0),
                    props.get("VH",      -20.0),
                    props.get("VV_mean", -15.0),
                    props.get("VH_mean", -20.0),
                    props.get("VV_std",    2.0),
                    props.get("VH_std",    2.0),
                ]
                all_features.append(row)
                all_changed.append(False)

            logger.info(f"Normal pixels sampled: {normal_count}")

    if not all_features:
        logger.warning("No features extracted")
        return np.array([]), np.array([])

    features   = np.array(all_features,  dtype=np.float32)
    is_changed = np.array(all_changed,   dtype=bool)

    # Normalise each feature to [0, 1]
    features[:, 0:2] = (np.clip(features[:, 0:2], -30, 0) + 30) / 30.0
    features[:, 2:4] = (np.clip(features[:, 2:4], -30, 0) + 30) / 30.0
    features[:, 4:6] = np.clip(features[:, 4:6] / 10.0, 0, 1)

    logger.success(
        f"Features extracted: {len(features)} samples | "
        f"changed={is_changed.sum()} normal={(~is_changed).sum()}"
    )
    return features, is_changed


# ============================================================
# Pseudo-labels
# ============================================================

def generate_pseudo_labels(
    features:   np.ndarray,
    is_changed: np.ndarray
) -> np.ndarray:
    """
    Generate weak labels from SAR feature statistics.

    Logic (no manual annotation needed):
      - Not changed                    → 0 normal_forest
      - Changed + high VV_std          → 2 structure  (high texture)
      - Changed + high VV, low VH      → 1 clearing   (bare ground)
      - Changed + low VV_std           → 3 path_or_track (linear, smooth)
    """
    labels = np.zeros(len(features), dtype=np.int64)

    vv      = features[:, 0]   # normalised VV
    vh      = features[:, 1]   # normalised VH
    vv_std  = features[:, 4]   # normalised VV std

    for i in range(len(features)):
        if not is_changed[i]:
            labels[i] = 0   # normal forest
        elif vv_std[i] > 0.6:
            labels[i] = 2   # structure — high texture
        elif vv[i] > 0.5 and vh[i] < 0.4:
            labels[i] = 1   # clearing — high VV, low VH
        else:
            labels[i] = 3   # path or track

    counts = {CLASS_NAMES[i]: int((labels == i).sum()) for i in range(4)}
    logger.info(f"Pseudo-labels: {counts}")
    return labels


# ============================================================
# Train
# ============================================================

def train_classifier(
    features:  np.ndarray,
    labels:    np.ndarray,
    config:    dict,
    save_path: str = None
) -> SARFeatureMLP:
    """Train the SAR feature MLP."""
    clf_cfg = config["classifier"]
    n       = len(features)
    n_train = int(n * clf_cfg["train_split"])
    idx     = np.random.permutation(n)

    train_ds = SARFeatureDataset(features[idx[:n_train]], labels[idx[:n_train]])
    val_ds   = SARFeatureDataset(features[idx[n_train:]], labels[idx[n_train:]])
    train_dl = DataLoader(train_ds, batch_size=clf_cfg["batch_size"], shuffle=True)
    val_dl   = DataLoader(val_ds,   batch_size=clf_cfg["batch_size"], shuffle=False)

    model     = SARFeatureMLP(num_classes=len(CLASS_NAMES))
    optimizer = optim.Adam(model.parameters(), lr=clf_cfg["learning_rate"])
    criterion = nn.CrossEntropyLoss()
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

    best_val_loss = float("inf")
    patience_ctr  = 0
    best_state    = None

    logger.info(
        f"Training MLP | train={len(train_ds)} val={len(val_ds)} | "
        f"epochs={clf_cfg['epochs']} bs={clf_cfg['batch_size']}"
    )

    for epoch in range(clf_cfg["epochs"]):
        model.train()
        train_loss = 0.0
        for xb, yb in train_dl:
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        train_loss /= len(train_dl)

        model.eval()
        val_loss = 0.0
        correct  = 0
        total    = 0
        with torch.no_grad():
            for xb, yb in val_dl:
                out       = model(xb)
                val_loss += criterion(out, yb).item()
                correct  += (out.argmax(1) == yb).sum().item()
                total    += len(yb)
        val_loss /= max(len(val_dl), 1)
        val_acc   = correct / total * 100 if total > 0 else 0

        scheduler.step(val_loss)

        if (epoch + 1) % 5 == 0 or epoch == 0:
            logger.info(
                f"Epoch {epoch+1:3d} | "
                f"train={train_loss:.4f} | "
                f"val={val_loss:.4f} | "
                f"acc={val_acc:.1f}%"
            )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_ctr  = 0
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            patience_ctr += 1
            if patience_ctr >= clf_cfg["early_stopping_patience"]:
                logger.info(f"Early stopping at epoch {epoch + 1}")
                break

    model.load_state_dict(best_state)
    logger.success(f"Training complete | best val_loss={best_val_loss:.4f}")

    if save_path:
        save_model(model, save_path, config, val_loss=best_val_loss)

    return model


# ============================================================
# Inference
# ============================================================

def classify_features(
    model:    SARFeatureMLP,
    features: np.ndarray,
) -> dict:
    """Run inference on extracted feature vectors."""
    model.eval()

    with torch.no_grad():
        logits = model(torch.tensor(features, dtype=torch.float32))
        probs  = torch.softmax(logits, dim=1).numpy()

    labels      = probs.argmax(axis=1)
    suspicious  = np.isin(labels, list(SUSPICIOUS_CLASSES))
    class_names = [CLASS_NAMES[l] for l in labels]

    counts = {CLASS_NAMES[i]: int((labels == i).sum()) for i in range(4)}
    logger.info(f"Results: {counts} | suspicious: {suspicious.sum()}/{len(features)}")

    return {
        "labels":      labels,
        "probs":       probs,
        "suspicious":  suspicious,
        "class_names": class_names
    }


# ============================================================
# Save / load
# ============================================================

def save_model(model, path, config, val_loss=None):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "model_state": model.state_dict(),
        "class_names": CLASS_NAMES,
        "val_loss":    val_loss,
        "saved_at":    datetime.utcnow().isoformat(),
        "config":      config["classifier"]
    }, out)
    logger.success(f"Model saved → {out}")
    return out


def load_model(path: str) -> SARFeatureMLP:
    ckpt  = torch.load(path, map_location="cpu")
    model = SARFeatureMLP(num_classes=len(CLASS_NAMES))
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    logger.success(f"Model loaded ← {path}")
    return model