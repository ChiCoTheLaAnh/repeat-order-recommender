"""Weighted pointwise ranking models and self-contained local model artifacts."""

import json
from pathlib import Path
import resource
import time
import warnings

import joblib
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from xgboost import XGBClassifier

from .audit_raw import ROOT
from .ranking_features import feature_matrix

CONFIG_PATH = ROOT / "config/ranking_models_v1.json"


def load_model_config(path=CONFIG_PATH):
    config = json.loads(Path(path).read_text())
    variants = config["xgboost_configs"]
    if not 1 <= len(variants) <= 3 or len({part["name"] for part in variants}) != len(variants):
        raise ValueError("Use one to three distinct predefined XGBoost configurations")
    if config["xgboost_common"]["scale_pos_weight"] != 1:
        raise ValueError("Initial milestone forbids class balancing")
    return config


class LogColumns(BaseEstimator, TransformerMixin):
    """Fixed, stateless transform; column indices come from the versioned schema."""
    def __init__(self, columns):
        self.columns = columns

    def fit(self, values, y=None):
        self.n_features_in_ = values.shape[1]
        return self

    def transform(self, values):
        result = np.array(values, dtype=np.float32, copy=True)
        result[:, self.columns] = np.log1p(result[:, self.columns])
        return result


def fit_model(name, examples, schema, config):
    if "split" in examples and not examples.split.eq("train").all():
        raise ValueError("Models may fit training examples only")
    weights = examples.row_weight.to_numpy(dtype=np.float64)
    totals = examples.groupby("query_id", sort=False).row_weight.sum()
    if not np.isfinite(weights).all() or np.any(weights <= 0) or not np.allclose(totals, 1):
        raise ValueError("Each nonempty training query must have total positive row weight one")
    labels = examples.label.to_numpy(dtype=np.int8)
    if set(labels) != {0, 1}:
        raise ValueError("Training requires both binary candidate-label classes")
    started = time.perf_counter()
    values = feature_matrix(examples, schema)
    columns = [schema["features"].index(column) for column in schema["log1p_features"]]
    steps = [("log", LogColumns(columns))]
    params = {"classifier__sample_weight": weights}
    if name == "logistic":
        classifier = LogisticRegression(**config["logistic"], random_state=config["seed"])
        steps += [("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
                  ("scaler", StandardScaler())]
        params["scaler__sample_weight"] = weights
        model_params = config["logistic"]
    else:
        variant = next((part for part in config["xgboost_configs"] if part["name"] == name), None)
        if variant is None:
            raise ValueError(f"Unknown predefined model: {name}")
        model_params = {**config["xgboost_common"], **{key: value for key, value in variant.items() if key != "name"},
                        "random_state": config["seed"], "n_jobs": config["threads"]}
        classifier = XGBClassifier(**model_params)
    steps.append(("classifier", classifier))
    pipeline = Pipeline(steps)
    with threadpool_limits(limits=config["threads"]), warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", ConvergenceWarning)
        pipeline.fit(values, labels, **params)
    if any(issubclass(warning.category, ConvergenceWarning) for warning in observed):
        raise RuntimeError("Logistic regression did not converge within the predefined iteration budget")
    return {"name": name, "pipeline": pipeline, "feature_schema": schema, "configuration": model_params,
        "threads": config["threads"], "seed": config["seed"], "training": {"rows": len(examples),
            "queries": int(examples.query_id.nunique()), "positive_rows": int(labels.sum()), "total_weight": float(weights.sum()),
            "seconds": time.perf_counter() - started, "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "cutoffs": sorted(examples.cutoff.astype(str).unique().tolist()) if "cutoff" in examples else [],
            "logistic_iterations": int(classifier.n_iter_[0]) if name == "logistic" else None,
            "sampling": "none", "class_balancing": "none"}}


def score_model(artifact, frame, batch_rows=100000):
    if batch_rows <= 0:
        raise ValueError("Scoring batch size must be positive")
    pipeline = artifact["pipeline"]
    output = []
    with threadpool_limits(limits=artifact["threads"]):
        for start in range(0, len(frame), batch_rows):
            values = feature_matrix(frame.iloc[start:start + batch_rows], artifact["feature_schema"])
            if artifact["name"] == "logistic":
                scores = pipeline.decision_function(values)
            else:
                scores = pipeline.predict(values, output_margin=True)
            if not np.isfinite(scores).all():
                raise ValueError("Model emitted nonfinite ranking scores")
            output.append(scores)
    return np.concatenate(output) if output else np.array([], dtype=np.float64)


def save_artifact(artifact, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path, compress=3)
    metadata = {key: value for key, value in artifact.items() if key != "pipeline"}
    pipeline = artifact["pipeline"]
    if artifact["name"] == "logistic":
        metadata["fitted_preprocessing"] = {"imputer_statistics": pipeline.named_steps["imputer"].statistics_.tolist(),
            "missing_indicator_columns": pipeline.named_steps["imputer"].indicator_.features_.tolist(),
            "weighted_scaler_mean": pipeline.named_steps["scaler"].mean_.tolist(),
            "weighted_scaler_scale": pipeline.named_steps["scaler"].scale_.tolist()}
    else:
        pipeline.named_steps["classifier"].save_model(path.with_suffix(".ubj"))
        metadata["fitted_preprocessing"] = "Stateless schema-ordered log1p; XGBoost uses native NaN branches"
    path.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")


def load_artifact(path):
    return joblib.load(path)
