from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from src.config import PROJECT_ROOT, load_config, resolve_path
from src.dashboard.alert_report import generate_alert_report
from src.dashboard.map_builder import build_risk_map
from src.detection.classifier import (
    N_FEATURES,
    classify_patches,
    generate_pseudo_labels,
    load_model,
    save_model,
    train_classifier,
)
from src.detection.risk_scorer import score_risk, top_alerts
from src.ingestion.grid import create_grid, tag_zones
from src.preprocessing.normalise import apply_standardiser, fit_standardiser


class ConfigAndGridTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_config_resolves_from_project_root(self):
        self.assertEqual(Path(self.config["_meta"]["project_root"]), PROJECT_ROOT)
        self.assertEqual(resolve_path(self.config, "models"), PROJECT_ROOT / "models")

    def test_grid_is_valid_and_zone_tagging_preserves_rows(self):
        grid = create_grid(self.config, zone="old_oyo_core")
        tagged = tag_zones(grid, self.config)
        self.assertFalse(tagged.empty)
        self.assertEqual(len(grid), len(tagged))
        self.assertTrue(tagged.geometry.is_valid.all())
        self.assertTrue(set(tagged["zone"]).issubset({"outside", *self.config["aoi"]["zones"]}))
        lon_min, lat_min, lon_max, lat_max = self.config["aoi"]["zones"]["old_oyo_core"]["bbox"]
        self.assertGreaterEqual(float(grid["lon_min"].min()), lon_min)
        self.assertLessEqual(float(grid["lon_max"].max()), lon_max)
        self.assertGreaterEqual(float(grid["lat_min"].min()), lat_min)
        self.assertLessEqual(float(grid["lat_max"].max()), lat_max)


class FeatureClassifierTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.config["classifier"] = dict(self.config["classifier"])
        self.config["classifier"].update({"epochs": 3, "early_stopping_patience": 2, "batch_size": 8})
        rng = np.random.default_rng(42)
        self.features = rng.normal(size=(48, N_FEATURES)).astype(np.float32)
        self.labels = generate_pseudo_labels(self.features)

    def test_standardiser_round_trip_shape(self):
        mean, scale = fit_standardiser(self.features)
        transformed = apply_standardiser(self.features, mean, scale)
        self.assertEqual(transformed.shape, self.features.shape)
        np.testing.assert_allclose(transformed.mean(axis=0), 0.0, atol=1e-5)

    def test_train_save_load_and_classify(self):
        model = train_classifier(self.features, self.labels, self.config)
        result = classify_patches(model, self.features[:5])
        self.assertEqual(result["probs"].shape, (5, 4))
        np.testing.assert_allclose(result["probs"].sum(axis=1), 1.0, atol=1e-6)
        with tempfile.TemporaryDirectory() as directory:
            path = save_model(model, Path(directory) / "model.pt", self.config)
            loaded = load_model(path)
            loaded_result = classify_patches(loaded, self.features[:5])
            np.testing.assert_allclose(result["probs"], loaded_result["probs"], atol=1e-6)


class RiskOutputTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.grid = gpd.GeoDataFrame(
            {
                "cell_id": [1, 2],
                "zone": ["old_oyo_core", "kwara_border"],
                "change_score": [0.9, 0.1],
                "classifier_score": [0.8, 0.2],
                "acled_score": [0.7, 0.0],
            },
            geometry=[box(3.8, 8.5, 3.85, 8.55), box(3.85, 8.5, 3.9, 8.55)],
            crs="EPSG:4326",
        )

    def test_risk_map_and_report(self):
        scored = score_risk(self.grid, self.config)
        self.assertGreater(scored.loc[0, "risk_score"], scored.loc[1, "risk_score"])
        self.assertEqual(int(top_alerts(scored, 1).iloc[0]["cell_id"]), 1)
        risk_map = build_risk_map(scored, self.config)
        self.assertIn("Risk cells", risk_map.get_root().render())
        with tempfile.TemporaryDirectory() as directory:
            report = generate_alert_report(scored, self.config, Path(directory) / "report.pdf")
            self.assertTrue(report.exists())
            self.assertGreater(report.stat().st_size, 500)


if __name__ == "__main__":
    unittest.main()