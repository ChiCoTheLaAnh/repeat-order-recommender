"""Past-only numeric candidate features; target labels are joined separately."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .audit_raw import ROOT
from .temporal_snapshots import recommendation_eligibility

SCHEMA_PATH = ROOT / "config/ranking_features_v1.json"
METADATA = {"customer_id", "sku", "query_id", "cutoff", "split", "target_end", "selected_source", "label", "row_weight"}
KEYS = ["cutoff", "query_id", "customer_id", "sku"]


def load_schema(path=SCHEMA_PATH):
    schema = json.loads(Path(path).read_text())
    validate_schema(schema)
    return schema


def validate_schema(schema):
    names = schema["features"]
    if len(names) != len(set(names)) or METADATA.intersection(names):
        raise ValueError("Duplicate or forbidden metadata/label model features")
    if not set(schema.get("log1p_features", [])).issubset(names):
        raise ValueError("Log-transform columns must belong to the ordered schema")


def feature_matrix(frame, schema):
    validate_schema(schema)
    values = frame.loc[:, schema["features"]].to_numpy(dtype=np.float32, copy=True)
    if np.isinf(values).any() or np.any(values[np.isfinite(values)] < 0):
        raise ValueError("Features must be nonnegative finite numbers or NaN for missing values")
    return values


def verify_temporal_separation(train_queries, validation_queries, train_labels):
    if train_queries.empty or validation_queries.empty:
        raise ValueError("Both temporal training and validation queries are required")
    if not train_queries.split.eq("train").all() or not validation_queries.split.eq("validation").all():
        raise ValueError("Only fixed train and validation splits may be used")
    first_validation = validation_queries.cutoff.min()
    for frame in [train_queries, train_labels]:
        if not frame.target_end.le(first_validation).all():
            raise ValueError("Temporal overlap: a training label window ends after the first validation cutoff")
    if not train_queries.cutoff.lt(first_validation).all():
        raise ValueError("Training cutoffs overlap validation")
    for frame in [train_queries, validation_queries]:
        ends = frame.cutoff + pd.offsets.MonthBegin(1)
        if not frame.target_end.eq(ends).all():
            raise ValueError("Snapshot target windows must end at the next calendar month")
    return {"latest_training_target_end": train_queries.target_end.max().isoformat(),
            "first_validation_cutoff": first_validation.isoformat(), "overlap": False}


def generate_features(paid, queries, candidates, schema):
    """One cutoff batch. Does not accept labels or future catalog metadata."""
    validate_schema(schema)
    if queries.empty or queries.cutoff.nunique() != 1:
        raise ValueError("Feature generation requires one nonempty cutoff's queries")
    cutoff = pd.Timestamp(queries.cutoff.iloc[0])
    if not candidates.cutoff.eq(cutoff).all() or candidates.customer_id.isna().any():
        raise ValueError("Candidates require identified customers at the same cutoff")
    if candidates.duplicated(["query_id", "sku"]).any():
        raise ValueError("Candidate pools must have unique SKUs per query")
    known_queries = queries.set_index("query_id").customer_id
    if not candidates.customer_id.eq(candidates.query_id.map(known_queries)).all():
        raise ValueError("Candidate identities must match the existing queries")
    # Filter event time BEFORE any eligibility, aggregation or item lookup.
    past = paid.loc[paid.invoice_date.lt(cutoff)]
    scope = recommendation_eligibility(past)
    history = past.loc[scope.recommendation_eligible_v1]
    personal = history.loc[history.customer_id.notna() & history.eligible_personalized_history.fillna(False)]
    recent_personal = personal.loc[personal.invoice_date.ge(cutoff - pd.DateOffset(days=180))]
    features = candidates.copy().reset_index(drop=True)
    ci = personal.groupby(["customer_id", "sku"], sort=True).agg(
        ci_invoice_count=("invoice_id", "nunique"), ci_last=("invoice_date", "max"))
    quantity = recent_personal.groupby(["customer_id", "sku"], sort=True).quantity.sum().rename("ci_quantity_180")
    features = features.join(ci, on=["customer_id", "sku"], validate="many_to_one").join(quantity, on=["customer_id", "sku"], validate="many_to_one")
    features["ci_previously_purchased"] = features.ci_last.notna().astype(int)
    features["ci_days_since_last_purchase"] = (cutoff - features.ci_last).dt.total_seconds() / 86400
    features[["ci_invoice_count", "ci_quantity_180"]] = features[["ci_invoice_count", "ci_quantity_180"]].fillna(0)
    last_item = history.groupby("sku", sort=True).invoice_date.max()
    if not set(features.sku).issubset(last_item.index):
        raise ValueError("Candidate SKU is absent from the pre-cutoff catalog")
    features["item_days_since_last_purchase"] = (cutoff - features.sku.map(last_item)).dt.total_seconds() / 86400
    identified = history.loc[history.customer_id.notna()]
    for days in [30, 180]:
        window = identified.loc[identified.invoice_date.ge(cutoff - pd.DateOffset(days=days))]
        purchasers = window.groupby("sku", sort=True).customer_id.nunique()
        features[f"item_purchasers_{days}"] = features.sku.map(purchasers).fillna(0)
    customer = recent_personal.groupby("customer_id", sort=True).agg(
        customer_invoice_count_180=("invoice_id", "nunique"), customer_sku_count_180=("sku", "nunique"),
        customer_last=("invoice_date", "max"))
    features = features.join(customer, on="customer_id", validate="many_to_one")
    features["customer_days_since_last_purchase_180"] = (cutoff - features.customer_last).dt.total_seconds() / 86400
    features[["customer_invoice_count_180", "customer_sku_count_180"]] = features[["customer_invoice_count_180", "customer_sku_count_180"]].fillna(0)
    features = features[KEYS + ["selected_source"] + schema["features"]].copy()
    features[schema["features"]] = features[schema["features"]].astype("float32")
    feature_matrix(features, schema)  # Verify values/order without bringing labels into this function.
    return features


def make_examples(features, labels):
    """Label existing rows only; every nonempty query contributes total weight one."""
    if labels.duplicated(["query_id", "sku"]).any():
        raise ValueError("Snapshot labels must be distinct query-SKU instances")
    examples = features.copy()
    positives = pd.MultiIndex.from_frame(labels[["query_id", "sku"]])
    pairs = pd.MultiIndex.from_frame(examples[["query_id", "sku"]])
    examples["label"] = pairs.isin(positives).astype("int8")
    examples["row_weight"] = 1.0 / examples.groupby("query_id", sort=False).query_id.transform("size")
    return examples
