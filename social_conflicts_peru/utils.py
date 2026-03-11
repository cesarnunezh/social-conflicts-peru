import json
import re

import polars as pl
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, precision_recall_fscore_support

ALLOWED_DIALOGO_LABELS = ("HAY_DIALOGO", "NO_DIALOGO")
DEFAULT_MODEL_PRICES = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
}

def normalize_label(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", text.upper()).strip()
    normalized = (
        cleaned.replace("Á", "A")
        .replace("É", "E")
        .replace("Í", "I")
        .replace("Ó", "O")
        .replace("Ú", "U")
        .replace(" ", "_")
    )
    if "NO_HAY_DIALOGO" in normalized or normalized == "NO_DIALOGO":
        return "NO_DIALOGO"
    if "HAY_DIALOGO" in normalized:
        return "HAY_DIALOGO"
    return None


def parse_plain_label(raw_text: str) -> tuple[str | None, bool, str | None]:
    label = normalize_label(raw_text)
    if label is None:
        return None, True, "invalid_plain_label"
    return label, False, None

def parse_json_label(raw_text: str) -> tuple[str | None, bool, str | None]:
    try:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None, True, "no_json_object"
        payload = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, True, f"json_decode_error:{exc.msg}"

    label = normalize_label(str(payload.get("label", "")))
    if label is None:
        return None, True, "invalid_json_label"

    return label, False, None

def summarize_prediction_df(
    prediction_df: pl.DataFrame,
    group_cols: list[str],
    label_col: str = "true_label",
    pred_col: str = "normalized_label",
) -> pl.DataFrame:
    rows = []

    for keys, group in prediction_df.group_by(group_cols, maintain_order=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        key_map = dict(zip(group_cols, keys, strict=False))

        y_true = group[label_col].to_list()
        y_pred = [label if label is not None else "__INVALID__" for label in group[pred_col].to_list()]

        precision, recall, f1, support = precision_recall_fscore_support(
            y_true,
            y_pred,
            labels=list(ALLOWED_DIALOGO_LABELS),
            zero_division=0,
        )
        macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
            y_true,
            y_pred,
            labels=list(ALLOWED_DIALOGO_LABELS),
            average="macro",
            zero_division=0,
        )

        row = {
            **key_map,
            "n_rows": group.height,
            "accuracy": accuracy_score(y_true, y_pred),
            "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
            "precision_macro": macro_precision,
            "recall_macro": macro_recall,
            "f1_macro": macro_f1,
        }

        for idx, label in enumerate(ALLOWED_DIALOGO_LABELS):
            short = label.lower()
            row[f"precision_{short}"] = precision[idx]
            row[f"recall_{short}"] = recall[idx]
            row[f"f1_{short}"] = f1[idx]
            row[f"support_{short}"] = int(support[idx])

        rows.append(row)

    return pl.DataFrame(rows).sort(group_cols)


def build_confusion_df(
    prediction_df: pl.DataFrame,
    group_col: str,
    label_col: str = "true_label",
    pred_col: str = "normalized_label",
) -> pl.DataFrame:
    rows = []

    for group_value, group in prediction_df.group_by(group_col, maintain_order=True):
        if isinstance(group_value, tuple):
            group_value = group_value[0]

        y_true = group[label_col].to_list()
        y_pred = [label if label is not None else "__INVALID__" for label in group[pred_col].to_list()]
        matrix = confusion_matrix(
            y_true,
            y_pred,
            labels=list(ALLOWED_DIALOGO_LABELS) + ["__INVALID__"],
        )

        labels = list(ALLOWED_DIALOGO_LABELS) + ["__INVALID__"]
        for true_idx, true_label in enumerate(labels):
            for pred_idx, pred_label in enumerate(labels):
                rows.append(
                    {
                        group_col: group_value,
                        "true_label": true_label,
                        "pred_label": pred_label,
                        "count": int(matrix[true_idx][pred_idx]),
                    }
                )

    return pl.DataFrame(rows)
