#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full-dataset quality check with resumable artifacts and in-place issue handling.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any, Iterable

from audit_training_data import (
    build_optional_stats,
    build_prompt,
    discover_game_background,
    load_jsonl as load_jsonl_file,
    load_prompt_template,
    normalize_label,
)
from command_registry_compact import compact_file, load_commands
from llm_client import call_llm_json, get_available_models

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "output"

DEFAULT_BATCH_SIZE = 20
CHECK_DIR_NAME = "full_data_check"
CHECK_VERSION = 1
ISSUE_REVIEW_DIR_NAME = "issue_review"
ISSUE_REVIEW_VERSION = 2

MANIFEST_FILE = "manifest.json"
SUMMARY_FILE = "summary.json"
SNAPSHOT_FILE = "dataset_snapshot.jsonl"
RESOLUTION_STATE_FILE = "resolution_state.json"
ACTION_LOG_FILE = "actions.jsonl"
BATCH_FILE_PATTERN = "batch_{batch_index:04d}.json"
ISSUE_REVIEW_MANIFEST_FILE = "manifest.json"
ISSUE_REVIEW_SUMMARY_FILE = "summary.json"
ISSUE_REVIEW_LOG_FILE = "reviews.jsonl"

SOURCE_FILE_TYPES: list[tuple[str, str]] = [
    ("template.jsonl", "template"),
    ("adversarial.jsonl", "adversarial"),
    ("paraphrase.jsonl", "paraphrase"),
]
SOURCE_TYPE_TO_FILENAME = {source_type: filename for filename, source_type in SOURCE_FILE_TYPES}

ISSUE_FILTERABLE_VERDICTS = {"pass", "borderline", "fail", "fatal"}
VALID_SAMPLE_LABELS = {"quick_command", "tactical", "chat"}
RESOLUTION_PENDING = "pending"
RESOLUTION_APPLIED = "applied"
RESOLUTION_IGNORED = "ignored"
RESOLUTION_CONFLICT = "conflict"
RESOLUTION_STATUSES = {
    RESOLUTION_PENDING,
    RESOLUTION_APPLIED,
    RESOLUTION_IGNORED,
    RESOLUTION_CONFLICT,
}
ACTION_APPLY_EXPECTED = "apply_expected"
ACTION_DELETE_SAMPLE = "delete_sample"
ACTION_IGNORE = "ignore"
ACTION_TYPES = {ACTION_APPLY_EXPECTED, ACTION_DELETE_SAMPLE, ACTION_IGNORE}
RECOMMENDED_ACTION_KEEP = "keep"
RECOMMENDED_ACTIONS = {
    RECOMMENDED_ACTION_KEEP,
    ACTION_APPLY_EXPECTED,
    ACTION_DELETE_SAMPLE,
}
REVIEW_DECISION_AUTO_PROCESS = "auto_process"
REVIEW_DECISION_PENDING_CONFIRMATION = "pending_confirmation"
REVIEW_DECISIONS = {
    REVIEW_DECISION_AUTO_PROCESS,
    REVIEW_DECISION_PENDING_CONFIRMATION,
}
REVIEW_ACTION_NONE = "none"
REVIEW_ACTIONS = {
    ACTION_APPLY_EXPECTED,
    ACTION_DELETE_SAMPLE,
    ACTION_IGNORE,
    REVIEW_ACTION_NONE,
}
REVIEW_CONFIDENCE_LEVELS = {"low", "medium", "high"}
REVIEW_CONSENSUS_MODE_STRICT_STRUCTURED_ACTION = "strict_structured_action"
REVIEW_AGREEMENT_AGREED_AUTO_PROCESS = "agreed_auto_process"
REVIEW_AGREEMENT_AGREED_PENDING_CONFIRMATION = "agreed_pending_confirmation"
REVIEW_AGREEMENT_MISMATCH = "mismatch"
REVIEW_AGREEMENT_PRIMARY_ERROR = "primary_error"
REVIEW_AGREEMENT_SECONDARY_ERROR = "secondary_error"
REVIEW_AGREEMENT_BOTH_ERROR = "both_error"
REVIEW_AGREEMENT_STATUSES = {
    REVIEW_AGREEMENT_AGREED_AUTO_PROCESS,
    REVIEW_AGREEMENT_AGREED_PENDING_CONFIRMATION,
    REVIEW_AGREEMENT_MISMATCH,
    REVIEW_AGREEMENT_PRIMARY_ERROR,
    REVIEW_AGREEMENT_SECONDARY_ERROR,
    REVIEW_AGREEMENT_BOTH_ERROR,
}
REVIEW_CONFIDENCE_RANKS = {
    "low": 0,
    "medium": 1,
    "high": 2,
}
TACTICAL_GLOBAL_NEGATIVE_BUCKETS = {
    "tactical_self_action",
    "tactical_team_plan",
    "tactical_conditional",
    "tactical_multi_step",
    "tactical_missing_slot",
    "tactical_out_of_registry_teammate_action",
    "tactical_ambiguous",
}
CHAT_GLOBAL_NEGATIVE_BUCKETS = {
    "chat_emotion",
    "chat_noise",
    "chat_out_of_game",
}
GLOBAL_NEGATIVE_BUCKETS = (
    TACTICAL_GLOBAL_NEGATIVE_BUCKETS
    | CHAT_GLOBAL_NEGATIVE_BUCKETS
)

PROMPT_EXTENSION = """

# Full Dataset Check Extra Requirements
你正在执行“全部数据检查”，结果会被直接用于页面处理和源文件回写。

额外要求：
1. `sample_results[*].sample_index` 必须严格使用当前批次内的 0-based 下标。
2. 在每个 `sample_result` 中额外输出：
   - `expected_slots`: 仅当 `expected_label = quick_command` 时填写对象；只能使用 `use` / `target` 两个键；值必须是原句中的完整可抽取子串。若不是 quick_command 或无法安全给出，则填空对象 `{}`
   - `recommended_action`: 只能是 `keep` / `apply_expected` / `delete_sample`
3. 推荐动作规则：
   - 语义正确且可保留：`keep`
   - 需要按期望标签 / command / slots 修正后继续保留：`apply_expected`
   - 样本不适合保留在训练集中，直接删掉更合理：`delete_sample`
4. 如果当前样本只是质量差、改写退化、近重复、表述不自然，但“期望标签 / command”与当前一致，优先推荐 `delete_sample`
5. 如果推荐 `apply_expected`，请尽量补齐 `expected_slots`

最终仍然返回与上文相同的顶层 JSON 结构，只是在每个 `sample_result` 中增加这些字段。
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练数据全部检查器")
    parser.add_argument("--game", default="mmorpg", help="游戏类型（commands/<game>.json）")
    parser.add_argument("--model", default=None, help="LLM 模型名")
    parser.add_argument(
        "--batch_size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批检查样本数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="重新开始：清空已有 full_data_check 工件后重跑",
    )
    parser.add_argument("--think_mode", action="store_true", help="开启思考模式")
    parser.add_argument("--think_level", default="high", help="思考等级（low/medium/high）")
    return parser.parse_args()


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def read_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return load_jsonl_file(str(path))


def get_game_dir(game: str) -> Path:
    return OUTPUT_DIR / game


def get_full_check_dir(game: str) -> Path:
    return get_game_dir(game) / CHECK_DIR_NAME


def get_manifest_path(game: str) -> Path:
    return get_full_check_dir(game) / MANIFEST_FILE


def get_summary_path(game: str) -> Path:
    return get_full_check_dir(game) / SUMMARY_FILE


def get_snapshot_path(game: str) -> Path:
    return get_full_check_dir(game) / SNAPSHOT_FILE


def get_resolution_state_path(game: str) -> Path:
    return get_full_check_dir(game) / RESOLUTION_STATE_FILE


def get_action_log_path(game: str) -> Path:
    return get_full_check_dir(game) / ACTION_LOG_FILE


def get_issue_review_dir(game: str) -> Path:
    return get_full_check_dir(game) / ISSUE_REVIEW_DIR_NAME


def get_issue_review_manifest_path(game: str) -> Path:
    return get_issue_review_dir(game) / ISSUE_REVIEW_MANIFEST_FILE


def get_issue_review_summary_path(game: str) -> Path:
    return get_issue_review_dir(game) / ISSUE_REVIEW_SUMMARY_FILE


def get_issue_review_log_path(game: str) -> Path:
    return get_issue_review_dir(game) / ISSUE_REVIEW_LOG_FILE


def get_batch_path(game: str, batch_index: int) -> Path:
    return get_full_check_dir(game) / BATCH_FILE_PATTERN.format(batch_index=batch_index)


def get_utterance(sample: dict) -> str:
    return sample.get("text") or sample.get("input") or ""


def normalize_command_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def normalize_slots(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key in ("use", "target"):
        slot_value = value.get(key)
        if isinstance(slot_value, str) and slot_value:
            normalized[key] = slot_value
    return normalized


def normalize_bucket(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def get_sample_bucket(sample: dict) -> str:
    meta = sample.get("meta")
    if not isinstance(meta, dict):
        return ""
    return normalize_bucket(meta.get("bucket"))


def set_sample_label(sample: dict, label: str) -> None:
    current = sample.get("label")
    if isinstance(current, dict):
        updated_label = dict(current)
        updated_label["type"] = label
        sample["label"] = updated_label
        return
    sample["label"] = label


def set_sample_bucket(sample: dict, bucket: str) -> None:
    meta = sample.get("meta")
    updated_meta = dict(meta) if isinstance(meta, dict) else {}
    updated_meta["bucket"] = bucket
    sample["meta"] = updated_meta


def validate_global_negative_bucket(label: str, bucket: str) -> None:
    if label == "tactical":
        if bucket not in TACTICAL_GLOBAL_NEGATIVE_BUCKETS:
            raise ValueError(f"无效的 tactical bucket: {bucket or '<empty>'}")
        return
    if label == "chat":
        if bucket not in CHAT_GLOBAL_NEGATIVE_BUCKETS:
            raise ValueError(f"无效的 chat bucket: {bucket or '<empty>'}")
        return
    raise ValueError(f"global_negative 只允许 tactical/chat，不能应用为: {label}")


def load_issue_review_prompt_template() -> str:
    path = SCRIPT_DIR / "prompts" / "full_check_issue_review_prompt.txt"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def command_slot_map(game: str) -> dict[str, list[str]]:
    commands_path = PROJECT_DIR / "commands" / f"{game}.json"
    commands = load_commands(str(commands_path))
    result: dict[str, list[str]] = {}
    for command in commands:
        slot_names: list[str] = []
        for slot in command.get("slots", []):
            name = slot.get("name")
            if name in {"use", "target"}:
                slot_names.append(name)
        result[command["command_id"]] = slot_names
    return result


def list_command_dirs(game_dir: Path) -> list[Path]:
    command_dirs: list[Path] = []
    if not game_dir.exists():
        return command_dirs
    for entry in sorted(game_dir.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        if any((entry / filename).exists() for filename, _ in SOURCE_FILE_TYPES):
            command_dirs.append(entry)
    return command_dirs


def iter_source_files(game: str) -> list[tuple[Path, str, str]]:
    game_dir = get_game_dir(game)
    files: list[tuple[Path, str, str]] = []
    for command_dir in list_command_dirs(game_dir):
        for filename, source_type in SOURCE_FILE_TYPES:
            path = command_dir / filename
            if path.exists():
                files.append((path, source_type, command_dir.name))

    global_negatives_path = game_dir / "global_negatives.jsonl"
    if global_negatives_path.exists():
        files.append((global_negatives_path, "global_negative", ""))
    return files


def file_relative_to_project(path: Path) -> str:
    return str(path.relative_to(PROJECT_DIR)).replace("\\", "/")


def sample_hash(sample: dict) -> str:
    return sha1_text(stable_json_dumps(sample))


def normalize_source_sample(sample: dict, source_type: str) -> dict:
    normalized = dict(sample)
    normalized.setdefault("source_type", source_type)
    return normalized


def collect_source_snapshot(game: str) -> dict:
    game_dir = get_game_dir(game)
    merged_all_path = game_dir / "merged_all.jsonl"
    if not merged_all_path.exists():
        raise FileNotFoundError(f"merged_all.jsonl 不存在: {merged_all_path}")

    snapshot_rows: list[dict] = []
    merged_from_sources: list[dict] = []
    source_files_meta: list[dict] = []

    dataset_index = 0
    for source_path, source_type, default_command_id in iter_source_files(game):
        file_rows: list[dict] = []
        with open(source_path, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                sample = json.loads(stripped)
                normalized_sample = normalize_source_sample(sample, source_type)
                current_hash = sample_hash(normalized_sample)
                source_command_id = normalize_command_id(
                    normalized_sample.get("source_command_id")
                    or normalized_sample.get("command_id")
                    or default_command_id
                )
                row = {
                    "sample_id": sha1_text(
                        f"{file_relative_to_project(source_path)}:{line_number}:{current_hash}"
                    ),
                    "dataset_index": dataset_index,
                    "source_file": file_relative_to_project(source_path),
                    "source_line_number": line_number,
                    "source_type": source_type,
                    "source_command_id": source_command_id,
                    "sample_hash": current_hash,
                    "sample": normalized_sample,
                }
                snapshot_rows.append(row)
                file_rows.append(row)
                merged_from_sources.append(normalized_sample)
                dataset_index += 1

        source_files_meta.append(
            {
                "path": file_relative_to_project(source_path),
                "source_type": source_type,
                "command_id": default_command_id,
                "line_count": len(file_rows),
                "file_hash": sha1_bytes(source_path.read_bytes()),
            }
        )

    merged_all_rows = load_jsonl(merged_all_path)
    if merged_from_sources != merged_all_rows:
        raise ValueError(
            "源文件与 merged_all.jsonl 不一致，请先重新执行合并输出后再进行全部数据检查。"
        )

    merged_all_hash = sha1_bytes(merged_all_path.read_bytes())
    dataset_fingerprint = sha1_text(
        stable_json_dumps(
            {
                "merged_all_hash": merged_all_hash,
                "source_files": source_files_meta,
                "total_samples": len(snapshot_rows),
            }
        )
    )

    return {
        "rows": snapshot_rows,
        "samples": merged_from_sources,
        "source_files": source_files_meta,
        "merged_all_path": file_relative_to_project(merged_all_path),
        "merged_all_hash": merged_all_hash,
        "dataset_fingerprint": dataset_fingerprint,
    }


def build_full_check_batch_context(
    game: str,
    input_path: str,
    batch_index: int,
    batch_count: int,
    batch_rows: list[dict],
) -> dict:
    labels: dict[str, int] = {}
    source_types: dict[str, int] = {}
    for row in batch_rows:
        sample = row["sample"]
        label = normalize_label(sample)
        source_type = sample.get("source_type", row["source_type"])
        labels[label] = labels.get(label, 0) + 1
        source_types[source_type] = source_types.get(source_type, 0) + 1

    return {
        "game": game,
        "dataset_scope": Path(input_path).name,
        "dataset_path": input_path,
        "covers_full_dataset": False,
        "full_check_mode": True,
        "audit_round_index": batch_index,
        "audit_rounds_total": batch_count,
        "batch_index": batch_index,
        "batch_count": batch_count,
        "sample_count_requested": len(batch_rows),
        "sample_count_actual": len(batch_rows),
        "sampling_strategy": "sequential_full_dataset_chunk",
        "dataset_index_range": {
            "start": batch_rows[0]["dataset_index"] if batch_rows else 0,
            "end": batch_rows[-1]["dataset_index"] if batch_rows else -1,
        },
        "batch_label_distribution": dict(sorted(labels.items())),
        "batch_source_type_distribution": dict(sorted(source_types.items())),
    }


def build_full_check_prompt(
    *,
    game_background: str,
    command_registry: str,
    sample_batch: list[dict],
    batch_context: dict,
    optional_stats: dict,
) -> str:
    base_prompt = build_prompt(
        template=load_prompt_template(),
        game_background=game_background,
        command_registry=command_registry,
        sample_batch=sample_batch,
        batch_context=batch_context,
        optional_stats=optional_stats,
    )
    return f"{base_prompt}\n\n{PROMPT_EXTENSION}\n"


def chunk_rows(rows: list[dict], batch_size: int) -> list[list[dict]]:
    return [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]


def normalize_expected_slots(
    *,
    sample_result: dict,
    batch_row: dict,
    required_slots: list[str],
) -> tuple[dict[str, str], bool]:
    expected_label = sample_result.get("expected_label")
    utterance = get_utterance(batch_row["sample"])
    if expected_label != "quick_command":
        return {}, False

    raw_slots = normalize_slots(sample_result.get("expected_slots"))
    normalized: dict[str, str] = {}
    for slot_name in required_slots:
        slot_value = raw_slots.get(slot_name)
        if slot_value and slot_value in utterance:
            normalized[slot_name] = slot_value

    current_command_id = normalize_command_id(batch_row["sample"].get("command_id"))
    expected_command_id = normalize_command_id(sample_result.get("expected_command_id"))
    current_slots = normalize_slots(batch_row["sample"].get("slots"))
    if expected_command_id and current_command_id == expected_command_id:
        for slot_name in required_slots:
            if slot_name in normalized:
                continue
            slot_value = current_slots.get(slot_name)
            if slot_value and slot_value in utterance:
                normalized[slot_name] = slot_value

    missing_required_slots = any(slot_name not in normalized for slot_name in required_slots)
    return normalized, missing_required_slots


def normalize_recommended_action(
    *,
    sample_result: dict,
    batch_row: dict,
    missing_required_slots: bool,
) -> str:
    raw_action = str(sample_result.get("recommended_action") or "").strip()
    if raw_action in RECOMMENDED_ACTIONS:
        if raw_action == ACTION_APPLY_EXPECTED and missing_required_slots:
            return ACTION_DELETE_SAMPLE
        return raw_action

    verdict = str(sample_result.get("verdict") or "unknown").strip()
    if verdict == "pass":
        return RECOMMENDED_ACTION_KEEP

    expected_label = str(sample_result.get("expected_label") or "").strip()
    expected_command_id = normalize_command_id(sample_result.get("expected_command_id"))
    current_label = normalize_label(batch_row["sample"])
    current_command_id = normalize_command_id(batch_row["sample"].get("command_id"))
    current_slots = normalize_slots(batch_row["sample"].get("slots"))
    expected_slots = normalize_slots(sample_result.get("expected_slots"))

    same_target = (
        expected_label == current_label
        and expected_command_id == current_command_id
        and expected_slots == current_slots
    )
    if same_target:
        source_type = batch_row["source_type"]
        return ACTION_DELETE_SAMPLE if source_type in {"paraphrase", "adversarial"} else RECOMMENDED_ACTION_KEEP

    if expected_label in {"quick_command", "tactical", "chat"}:
        if expected_label == "quick_command" and missing_required_slots:
            return ACTION_DELETE_SAMPLE
        return ACTION_APPLY_EXPECTED

    return ACTION_DELETE_SAMPLE


def issue_summary(sample_result: dict) -> str:
    issues = sample_result.get("issues")
    if isinstance(issues, list) and issues:
        parts: list[str] = []
        for issue in issues[:3]:
            if not isinstance(issue, dict):
                continue
            issue_type = str(issue.get("type") or "issue")
            detail = str(issue.get("detail") or "").strip()
            parts.append(f"{issue_type}: {detail}" if detail else issue_type)
        if parts:
            return " | ".join(parts)
    reason = str(sample_result.get("reason") or "").strip()
    if reason:
        return reason[:220]
    return ""


def enrich_sample_result(
    *,
    sample_result: dict,
    batch_row: dict,
    slot_map: dict[str, list[str]],
) -> dict:
    expected_command_id = normalize_command_id(sample_result.get("expected_command_id"))
    required_slots = slot_map.get(expected_command_id, []) if expected_command_id else []
    expected_slots, missing_required_slots = normalize_expected_slots(
        sample_result=sample_result,
        batch_row=batch_row,
        required_slots=required_slots,
    )
    recommended_action = normalize_recommended_action(
        sample_result=sample_result,
        batch_row=batch_row,
        missing_required_slots=missing_required_slots,
    )
    current_sample = batch_row["sample"]

    return {
        **sample_result,
        "sample_id": batch_row["sample_id"],
        "dataset_index": batch_row["dataset_index"],
        "source_file": batch_row["source_file"],
        "source_line_number": batch_row["source_line_number"],
        "sample_hash": batch_row["sample_hash"],
        "source_type": current_sample.get("source_type", batch_row["source_type"]),
        "source_command_id": batch_row["source_command_id"],
        "utterance": sample_result.get("utterance") or get_utterance(current_sample),
        "current_label": sample_result.get("current_label") or normalize_label(current_sample),
        "current_command_id": normalize_command_id(
            sample_result.get("current_command_id") or current_sample.get("command_id")
        ),
        "current_slots": normalize_slots(current_sample.get("slots")),
        "current_bucket": get_sample_bucket(current_sample) or None,
        "expected_command_id": expected_command_id or None,
        "expected_slots": expected_slots,
        "recommended_action": recommended_action,
        "can_apply_expected": (
            recommended_action == ACTION_APPLY_EXPECTED
            and not missing_required_slots
        ),
        "issue_summary": issue_summary(sample_result),
    }


def enrich_batch_sample_results(
    *,
    raw_results: list[dict],
    batch_rows: list[dict],
    slot_map: dict[str, list[str]],
) -> list[dict]:
    enriched: list[dict] = []
    for fallback_index, item in enumerate(raw_results):
        if not isinstance(item, dict):
            continue
        raw_index = item.get("sample_index")
        resolved_index = raw_index if isinstance(raw_index, int) else fallback_index
        if not isinstance(resolved_index, int) or not (0 <= resolved_index < len(batch_rows)):
            if isinstance(raw_index, int) and 1 <= raw_index <= len(batch_rows):
                resolved_index = raw_index - 1
            else:
                resolved_index = fallback_index if fallback_index < len(batch_rows) else None
        if resolved_index is None or not (0 <= resolved_index < len(batch_rows)):
            continue
        batch_row = batch_rows[resolved_index]
        item = dict(item)
        item["sample_index"] = resolved_index
        enriched.append(
            enrich_sample_result(
                sample_result=item,
                batch_row=batch_row,
                slot_map=slot_map,
            )
        )
    return enriched


def batch_issue_count(sample_results: list[dict]) -> int:
    return sum(1 for item in sample_results if item.get("verdict") in {"borderline", "fail", "fatal"})


def write_manifest(game: str, manifest: dict) -> None:
    manifest["updated_at"] = utc_now_iso()
    write_json_file(get_manifest_path(game), manifest)


def load_manifest(game: str) -> dict | None:
    return read_json_file(get_manifest_path(game))


def load_snapshot_rows(game: str) -> list[dict]:
    return load_jsonl(get_snapshot_path(game))


def ensure_resolution_state(game: str) -> dict[str, dict]:
    path = get_resolution_state_path(game)
    state = read_json_file(path, default={})
    if not isinstance(state, dict):
        state = {}
    write_json_file(path, state)
    return state


def load_resolution_state(game: str) -> dict[str, dict]:
    state = read_json_file(get_resolution_state_path(game), default={})
    return state if isinstance(state, dict) else {}


def list_batch_paths(game: str) -> list[Path]:
    return sorted(get_full_check_dir(game).glob("batch_*.json"))


def load_batch_payloads(game: str) -> list[dict]:
    payloads: list[dict] = []
    for path in list_batch_paths(game):
        payload = read_json_file(path)
        if isinstance(payload, dict):
            payload["file_name"] = path.name
            payloads.append(payload)
    return payloads


def build_summary_payload(game: str) -> dict:
    manifest = load_manifest(game)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(f"{MANIFEST_FILE} 不存在")

    batch_payloads = load_batch_payloads(game)
    resolution_state = load_resolution_state(game)

    batch_summaries: list[dict] = []
    total_issues = 0
    resolution_counts = {status: 0 for status in RESOLUTION_STATUSES}

    for payload in batch_payloads:
        sample_results = payload.get("sample_results", [])
        issue_results = [
            item for item in sample_results
            if isinstance(item, dict) and item.get("verdict") in {"borderline", "fail", "fatal"}
        ]
        total_issues += len(issue_results)
        for issue in issue_results:
            state = resolution_state.get(issue["sample_id"], {})
            status = state.get("status", RESOLUTION_PENDING)
            resolution_counts[status] = resolution_counts.get(status, 0) + 1

        summary = payload.get("audit_summary", {}) if isinstance(payload.get("audit_summary"), dict) else {}
        batch_summaries.append(
            {
                "batch_index": payload.get("batch_index"),
                "status": payload.get("status", "unknown"),
                "sample_count": payload.get("sample_count", 0),
                "range_start": payload.get("range_start", 0),
                "range_end": payload.get("range_end", -1),
                "updated_at": payload.get("updated_at"),
                "overall_risk": summary.get("overall_risk", "unknown"),
                "final_verdict": summary.get("final_verdict", "unknown"),
                "fail_count": summary.get("fail_count", 0),
                "borderline_count": summary.get("borderline_count", 0),
                "issue_count": len(issue_results),
                "error": payload.get("error", ""),
            }
        )

    completed_batches = sum(1 for item in batch_summaries if item["status"] == "completed")
    failed_batches = sum(1 for item in batch_summaries if item["status"] == "error")
    running_batches = sum(1 for item in batch_summaries if item["status"] == "running")
    pending_batches = max(manifest.get("batch_count", 0) - completed_batches - failed_batches - running_batches, 0)

    manifest_status = manifest.get("status", "unknown")
    if completed_batches == manifest.get("batch_count", 0) and failed_batches == 0 and running_batches == 0:
        manifest_status = "completed"
    elif completed_batches > 0 and failed_batches > 0 and running_batches == 0:
        manifest_status = "completed_with_errors"
    elif running_batches > 0:
        manifest_status = "running"

    summary = {
        "status": manifest_status,
        "game": game,
        "dataset_fingerprint": manifest.get("dataset_fingerprint"),
        "total_samples": manifest.get("total_samples", 0),
        "batch_size": manifest.get("batch_size", DEFAULT_BATCH_SIZE),
        "batch_count": manifest.get("batch_count", 0),
        "completed_batches": completed_batches,
        "failed_batches": failed_batches,
        "running_batches": running_batches,
        "pending_batches": pending_batches,
        "total_issues": total_issues,
        "resolution_counts": resolution_counts,
        "batches": batch_summaries,
        "created_at": manifest.get("created_at"),
        "updated_at": utc_now_iso(),
        "latest_batch_updated_at": max(
            (item.get("updated_at") or "" for item in batch_summaries),
            default="",
        ) or None,
    }
    write_json_file(get_summary_path(game), summary)

    manifest["status"] = manifest_status
    manifest["completed_batches"] = completed_batches
    manifest["failed_batches"] = failed_batches
    manifest["pending_batches"] = pending_batches
    write_manifest(game, manifest)
    return summary


def build_issue_index(game: str) -> dict[str, dict]:
    issue_index: dict[str, dict] = {}
    for payload in load_batch_payloads(game):
        sample_results = payload.get("sample_results", [])
        findings = payload.get("systemic_findings", [])
        for item in sample_results:
            if not isinstance(item, dict):
                continue
            if item.get("verdict") not in {"borderline", "fail", "fatal"}:
                continue
            issue_index[item["sample_id"]] = {
                **item,
                "batch_index": payload.get("batch_index"),
                "batch_status": payload.get("status"),
                "batch_updated_at": payload.get("updated_at"),
                "systemic_findings": findings if isinstance(findings, list) else [],
            }
    return issue_index


def merge_issue_resolution_state(issue: dict, resolution_state: dict[str, dict]) -> dict:
    state = resolution_state.get(issue["sample_id"], {})
    merged = dict(issue)
    merged["resolution_status"] = state.get("status", RESOLUTION_PENDING)
    merged["resolution_action"] = state.get("action")
    merged["resolution_message"] = state.get("message", "")
    merged["resolution_updated_at"] = state.get("updated_at")
    return merged


def load_issue_review_manifest(game: str) -> dict | None:
    payload = read_json_file(get_issue_review_manifest_path(game), default=None)
    return payload if isinstance(payload, dict) else None


def write_issue_review_manifest(game: str, manifest: dict) -> None:
    manifest["updated_at"] = utc_now_iso()
    write_json_file(get_issue_review_manifest_path(game), manifest)


def load_issue_review_entries(game: str) -> list[dict]:
    return [
        item for item in load_jsonl(get_issue_review_log_path(game))
        if isinstance(item, dict)
    ]


def load_issue_review_map(game: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for entry in load_issue_review_entries(game):
        sample_id = str(entry.get("sample_id") or "").strip()
        if sample_id:
            result[sample_id] = entry
    return result


def build_issue_review_summary_payload(game: str) -> dict | None:
    review_dir = get_issue_review_dir(game)
    review_manifest = load_issue_review_manifest(game)
    review_map = load_issue_review_map(game)

    if not review_dir.exists() and not review_map and not review_manifest:
        return None

    summary = {
        "game": game,
        "reviewed_total": len(review_map),
        "auto_processed": 0,
        "pending_confirmation": 0,
        "auto_ignored": 0,
        "auto_applied": 0,
        "apply_conflicts": 0,
        "dual_model_agree_auto": 0,
        "dual_model_agree_pending": 0,
        "dual_model_disagree": 0,
        "dual_model_errors": 0,
        "created_at": review_manifest.get("created_at") if isinstance(review_manifest, dict) else None,
        "updated_at": utc_now_iso(),
    }

    for entry in review_map.values():
        decision = str(entry.get("decision") or "").strip()
        action = str(entry.get("action") or "").strip()
        execution_status = str(entry.get("execution_status") or "").strip()
        agreement_status = str(entry.get("agreement_status") or "").strip()
        if decision == REVIEW_DECISION_PENDING_CONFIRMATION:
            summary["pending_confirmation"] += 1
        if execution_status in {RESOLUTION_APPLIED, RESOLUTION_IGNORED}:
            summary["auto_processed"] += 1
        if execution_status == RESOLUTION_IGNORED and action == ACTION_IGNORE:
            summary["auto_ignored"] += 1
        if execution_status == RESOLUTION_APPLIED and action in {ACTION_APPLY_EXPECTED, ACTION_DELETE_SAMPLE}:
            summary["auto_applied"] += 1
        if execution_status == RESOLUTION_CONFLICT:
            summary["apply_conflicts"] += 1
        if agreement_status == REVIEW_AGREEMENT_AGREED_AUTO_PROCESS:
            summary["dual_model_agree_auto"] += 1
        elif agreement_status == REVIEW_AGREEMENT_AGREED_PENDING_CONFIRMATION:
            summary["dual_model_agree_pending"] += 1
        elif agreement_status == REVIEW_AGREEMENT_MISMATCH:
            summary["dual_model_disagree"] += 1
        elif agreement_status in {
            REVIEW_AGREEMENT_PRIMARY_ERROR,
            REVIEW_AGREEMENT_SECONDARY_ERROR,
            REVIEW_AGREEMENT_BOTH_ERROR,
        }:
            summary["dual_model_errors"] += 1

    write_json_file(get_issue_review_summary_path(game), summary)
    return summary


def normalize_review_detail_payload(detail: Any) -> dict | None:
    if not isinstance(detail, dict):
        return None
    raw_notes = detail.get("safety_notes")
    return {
        "model": str(detail.get("model") or "").strip() or None,
        "decision": str(detail.get("decision") or "").strip() or None,
        "action": str(detail.get("action") or "").strip() or None,
        "confidence": str(detail.get("confidence") or "").strip().lower() or None,
        "final_label": str(detail.get("final_label") or "").strip() or None,
        "final_command_id": normalize_command_id(detail.get("final_command_id")) or None,
        "final_slots": normalize_slots(detail.get("final_slots")),
        "final_bucket": normalize_bucket(detail.get("final_bucket")) or None,
        "reason": str(detail.get("reason") or "").strip(),
        "pending_reason": str(detail.get("pending_reason") or "").strip(),
        "demotion_reason": str(detail.get("demotion_reason") or "").strip(),
        "error_message": str(detail.get("error_message") or "").strip(),
        "safety_notes": [
            str(item).strip()
            for item in (raw_notes if isinstance(raw_notes, list) else [])
            if str(item).strip()
        ],
    }


def merge_issue_review_state(issue: dict, review_state: dict[str, dict]) -> dict:
    review = review_state.get(issue["sample_id"], {})
    merged = dict(issue)
    merged["review_decision"] = review.get("decision")
    merged["review_action"] = review.get("action")
    merged["review_confidence"] = review.get("confidence")
    merged["review_reason"] = review.get("reason", "")
    merged["review_pending_reason"] = review.get("pending_reason", "")
    merged["review_final_label"] = review.get("final_label")
    merged["review_final_command_id"] = review.get("final_command_id")
    merged["review_final_slots"] = normalize_slots(review.get("final_slots"))
    merged["review_final_bucket"] = review.get("final_bucket")
    merged["review_safety_notes"] = (
        review.get("safety_notes")
        if isinstance(review.get("safety_notes"), list)
        else []
    )
    merged["review_execution_status"] = review.get("execution_status")
    merged["review_execution_message"] = review.get("execution_message", "")
    merged["review_updated_at"] = review.get("updated_at")
    merged["review_primary_model"] = review.get("primary_model")
    merged["review_secondary_model"] = review.get("secondary_model")
    merged["review_agreement_status"] = review.get("agreement_status")
    merged["review_disagreement_fields"] = (
        review.get("disagreement_fields")
        if isinstance(review.get("disagreement_fields"), list)
        else []
    )
    merged["review_primary_result"] = normalize_review_detail_payload(review.get("primary_review"))
    merged["review_secondary_result"] = normalize_review_detail_payload(review.get("secondary_review"))
    return merged


def get_full_check_overview(game: str) -> dict:
    check_dir = get_full_check_dir(game)
    if not check_dir.exists():
        return {
            "exists": False,
            "game": game,
            "output_dir": str(check_dir),
            "manifest": None,
            "summary": None,
            "review_summary": None,
            "can_resume": False,
        }

    manifest = load_manifest(game)
    summary = None
    if manifest:
        summary = build_summary_payload(game)

    can_resume = False
    if isinstance(manifest, dict):
        can_resume = (
            manifest.get("status") in {"running", "completed_with_errors"}
            or manifest.get("pending_batches", 0) > 0
            or manifest.get("failed_batches", 0) > 0
        )

    resolution_state = load_resolution_state(game)
    review_summary = build_issue_review_summary_payload(game)
    issue_index = build_issue_index(game)
    unresolved_count = sum(
        1 for sample_id in issue_index
        if resolution_state.get(sample_id, {}).get("status", RESOLUTION_PENDING) in {RESOLUTION_PENDING, RESOLUTION_CONFLICT}
    )

    return {
        "exists": True,
        "game": game,
        "output_dir": str(check_dir),
        "manifest": manifest,
        "summary": summary,
        "review_summary": review_summary,
        "can_resume": can_resume,
        "unresolved_count": unresolved_count,
    }


def get_full_check_issues(
    game: str,
    *,
    resolution_status: str | None = None,
    verdict: str | None = None,
    source_type: str | None = None,
    q: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict:
    manifest = load_manifest(game)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(f"{MANIFEST_FILE} 不存在")

    resolution_filters = {
        item.strip()
        for item in (resolution_status or "").split(",")
        if item.strip()
    }
    verdict_filters = {
        item.strip()
        for item in (verdict or "").split(",")
        if item.strip()
    }
    source_filters = {
        item.strip()
        for item in (source_type or "").split(",")
        if item.strip()
    }
    keyword = q.strip().lower()

    resolution_state = load_resolution_state(game)
    review_state = load_issue_review_map(game)
    issues = [
        merge_issue_review_state(
            merge_issue_resolution_state(issue, resolution_state),
            review_state,
        )
        for issue in build_issue_index(game).values()
    ]
    issues.sort(key=lambda item: (item.get("dataset_index", 0), item.get("batch_index", 0)))

    filtered: list[dict] = []
    for issue in issues:
        if resolution_filters and issue.get("resolution_status") not in resolution_filters:
            continue
        if verdict_filters and issue.get("verdict") not in verdict_filters:
            continue
        if source_filters and issue.get("source_type") not in source_filters:
            continue
        if keyword:
            haystack = " ".join(
                [
                    str(issue.get("utterance") or ""),
                    str(issue.get("issue_summary") or ""),
                    str(issue.get("reason") or ""),
                    str(issue.get("fix_suggestion") or ""),
                    str(issue.get("review_reason") or ""),
                    str(issue.get("review_pending_reason") or ""),
                    str(issue.get("review_execution_message") or ""),
                    str(issue.get("source_file") or ""),
                ]
            ).lower()
            if keyword not in haystack:
                continue
        filtered.append(issue)

    sliced = filtered[offset:offset + limit]
    return {
        "game": game,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "issues": sliced,
        "filters": {
            "resolution_status": sorted(resolution_filters),
            "verdict": sorted(verdict_filters),
            "source_type": sorted(source_filters),
            "q": q,
        },
    }


def build_manifest(
    *,
    game: str,
    model: str | None,
    think_mode: bool,
    think_level: str,
    batch_size: int,
    snapshot: dict,
) -> dict:
    total_samples = len(snapshot["rows"])
    return {
        "version": CHECK_VERSION,
        "task_type": "full_data_check",
        "status": "running",
        "game": game,
        "model": model,
        "think_mode": think_mode,
        "think_level": think_level,
        "batch_size": batch_size,
        "total_samples": total_samples,
        "batch_count": math.ceil(total_samples / batch_size) if total_samples else 0,
        "dataset_fingerprint": snapshot["dataset_fingerprint"],
        "merged_all_path": snapshot["merged_all_path"],
        "merged_all_hash": snapshot["merged_all_hash"],
        "source_files": snapshot["source_files"],
        "prompt_hash": sha1_text(load_prompt_template() + "\n" + PROMPT_EXTENSION),
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def validate_resume(manifest: dict, *, batch_size: int, snapshot: dict, model: str | None, think_mode: bool, think_level: str) -> None:
    if manifest.get("dataset_fingerprint") != snapshot["dataset_fingerprint"]:
        raise ValueError("数据集已变化，不能继续旧的全部数据检查，请勾选重新开始。")
    if manifest.get("batch_size") != batch_size:
        raise ValueError("batch_size 与现有任务不一致，不能续跑，请勾选重新开始。")
    if manifest.get("model") != model:
        raise ValueError("model 与现有任务不一致，不能续跑，请勾选重新开始。")
    if bool(manifest.get("think_mode")) != bool(think_mode):
        raise ValueError("think_mode 与现有任务不一致，不能续跑，请勾选重新开始。")
    if manifest.get("think_level") != think_level:
        raise ValueError("think_level 与现有任务不一致，不能续跑，请勾选重新开始。")
    if manifest.get("prompt_hash") != sha1_text(load_prompt_template() + "\n" + PROMPT_EXTENSION):
        raise ValueError("提示词版本已变化，不能续跑，请勾选重新开始。")


def prepare_full_check_run(
    *,
    game: str,
    model: str | None,
    think_mode: bool,
    think_level: str,
    batch_size: int,
    restart: bool,
) -> tuple[dict, list[dict]]:
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")

    output_dir = get_full_check_dir(game)
    if restart and output_dir.exists():
        shutil.rmtree(output_dir)

    snapshot = collect_source_snapshot(game)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(game)
    rows = snapshot["rows"]
    if manifest:
        validate_resume(
            manifest,
            batch_size=batch_size,
            snapshot=snapshot,
            model=model,
            think_mode=think_mode,
            think_level=think_level,
        )
        if not get_snapshot_path(game).exists():
            write_jsonl(get_snapshot_path(game), rows)
        ensure_resolution_state(game)
        return manifest, rows

    manifest = build_manifest(
        game=game,
        model=model,
        think_mode=think_mode,
        think_level=think_level,
        batch_size=batch_size,
        snapshot=snapshot,
    )
    write_manifest(game, manifest)
    write_jsonl(get_snapshot_path(game), rows)
    write_json_file(get_resolution_state_path(game), {})
    return manifest, rows


def rebuild_command_outputs(game: str, affected_commands: set[str] | None = None) -> None:
    game_dir = get_game_dir(game)
    all_merged_samples: list[dict] = []

    for command_dir in list_command_dirs(game_dir):
        command_id = command_dir.name
        if affected_commands is not None and command_id not in affected_commands:
            current_command_samples = load_jsonl(command_dir / "merged.jsonl")
            all_merged_samples.extend(current_command_samples)
            continue

        command_samples: list[dict] = []
        for filename, source_type in SOURCE_FILE_TYPES:
            path = command_dir / filename
            if not path.exists():
                continue
            for sample in load_jsonl(path):
                command_samples.append(normalize_source_sample(sample, source_type))

        merged_path = command_dir / "merged.jsonl"
        write_jsonl(merged_path, command_samples)
        all_merged_samples.extend(command_samples)

    global_negatives_path = game_dir / "global_negatives.jsonl"
    if global_negatives_path.exists():
        all_merged_samples.extend(
            [normalize_source_sample(sample, "global_negative") for sample in load_jsonl(global_negatives_path)]
        )

    write_jsonl(game_dir / "merged_all.jsonl", all_merged_samples)


def find_current_source_row(
    *,
    game: str,
    issue: dict,
) -> tuple[list[dict], int]:
    source_path = PROJECT_DIR / issue["source_file"]
    if not source_path.exists():
        raise FileNotFoundError(f"源文件不存在: {source_path}")

    rows = load_jsonl(source_path)
    original_line_number = int(issue["source_line_number"])
    target_hash = issue["sample_hash"]

    preferred_index = original_line_number - 1
    if 0 <= preferred_index < len(rows):
        candidate = normalize_source_sample(rows[preferred_index], issue["source_type"])
        if sample_hash(candidate) == target_hash:
            return rows, preferred_index

    matching_indices = [
        idx for idx, row in enumerate(rows)
        if sample_hash(normalize_source_sample(row, issue["source_type"])) == target_hash
    ]
    if len(matching_indices) == 1:
        return rows, matching_indices[0]
    if not matching_indices:
        raise ValueError(f"样本已变化，无法定位: {issue['sample_id']}")
    raise ValueError(f"样本存在多处重复，无法安全定位: {issue['sample_id']}")


def build_effective_issue_for_action(issue: dict, action: dict) -> dict:
    effective = dict(issue)

    if "expected_label" in action:
        value = str(action.get("expected_label") or "").strip()
        effective["expected_label"] = value
    if "expected_command_id" in action:
        effective["expected_command_id"] = normalize_command_id(action.get("expected_command_id")) or None
    if "expected_slots" in action:
        effective["expected_slots"] = normalize_slots(action.get("expected_slots"))
    if "expected_bucket" in action:
        effective["expected_bucket"] = normalize_bucket(action.get("expected_bucket")) or None
    return effective


def describe_expected_state(issue: dict) -> str:
    expected_label = str(issue.get("expected_label") or "").strip() or "unknown"
    if expected_label == "quick_command":
        return (
            f"label={expected_label}, "
            f"command_id={normalize_command_id(issue.get('expected_command_id')) or '<empty>'}, "
            f"slots={stable_json_dumps(normalize_slots(issue.get('expected_slots')))}"
        )
    if str(issue.get("source_type") or "") == "global_negative":
        return (
            f"label={expected_label}, "
            f"bucket={normalize_bucket(issue.get('expected_bucket')) or '<empty>'}"
        )
    return f"label={expected_label}"


def apply_expected_to_sample(sample: dict, issue: dict, slot_map: dict[str, list[str]]) -> dict:
    expected_label = issue.get("expected_label")
    if expected_label not in VALID_SAMPLE_LABELS:
        raise ValueError(f"无效的 expected_label: {expected_label}")

    updated = dict(sample)
    source_type = str(issue.get("source_type") or "")
    utterance = get_utterance(updated)

    if source_type == "global_negative":
        if expected_label == "quick_command":
            raise ValueError("global_negative 样本不能直接应用为 quick_command，请人工确认")
        expected_bucket = normalize_bucket(issue.get("expected_bucket"))
        validate_global_negative_bucket(expected_label, expected_bucket)
        set_sample_label(updated, expected_label)
        set_sample_bucket(updated, expected_bucket)
        updated.pop("command_id", None)
        updated.pop("slots", None)
        return updated

    set_sample_label(updated, expected_label)

    if expected_label == "quick_command":
        expected_command_id = normalize_command_id(issue.get("expected_command_id"))
        if not expected_command_id:
            raise ValueError("缺少 expected_command_id，无法应用建议")
        required_slots = slot_map.get(expected_command_id)
        if required_slots is None:
            raise ValueError(f"未知的 expected_command_id: {expected_command_id}")

        expected_slots = normalize_slots(issue.get("expected_slots"))
        missing_required_slots = [
            slot_name for slot_name in required_slots
            if slot_name not in expected_slots
        ]
        if missing_required_slots:
            raise ValueError(f"缺少 required slots: {', '.join(missing_required_slots)}")
        for slot_name, slot_value in expected_slots.items():
            if slot_value not in utterance:
                raise ValueError(f"slot `{slot_name}` 不是原句可抽取子串: {slot_value}")

        updated["command_id"] = expected_command_id
        updated["slots"] = {slot_name: expected_slots[slot_name] for slot_name in required_slots}
    else:
        updated["command_id"] = ""
        updated["slots"] = {}

    return updated


def resolve_apply_target_file(game: str, issue: dict, updated_sample: dict) -> str:
    source_type = str(issue.get("source_type") or "")
    expected_label = normalize_label(updated_sample)

    if source_type == "global_negative":
        if expected_label == "quick_command":
            raise ValueError("global_negative 样本不能直接应用为 quick_command，请人工确认")
        return issue["source_file"]

    if expected_label != "quick_command":
        return issue["source_file"]

    target_command_id = normalize_command_id(updated_sample.get("command_id"))
    if not target_command_id:
        raise ValueError("缺少目标 command_id，无法应用建议")

    filename = SOURCE_TYPE_TO_FILENAME.get(source_type)
    if not filename:
        raise ValueError(f"不支持的 source_type: {source_type}")

    return file_relative_to_project(get_game_dir(game) / target_command_id / filename)


def update_resolution_state(
    game: str,
    *,
    sample_id: str,
    status: str,
    action: str,
    message: str = "",
) -> None:
    state = load_resolution_state(game)
    state[sample_id] = {
        "status": status,
        "action": action,
        "message": message,
        "updated_at": utc_now_iso(),
    }
    write_json_file(get_resolution_state_path(game), state)


def apply_full_check_actions(game: str, actions: list[dict]) -> dict:
    if not actions:
        raise ValueError("actions 不能为空")

    issue_index = build_issue_index(game)
    if not issue_index:
        raise FileNotFoundError("未找到全部数据检查结果")

    slot_map = command_slot_map(game)
    affected_commands: set[str] = set()
    affected_global_negative = False
    pending_appends: dict[str, list[dict]] = {}
    results: list[dict] = []

    grouped_mutations: dict[str, list[dict]] = {}
    for action in actions:
        sample_id = str(action.get("sample_id") or "").strip()
        action_name = str(action.get("action") or "").strip()
        resolution_message = str(action.get("resolution_message") or "").strip()
        if not sample_id:
            raise ValueError("sample_id 不能为空")
        if action_name not in ACTION_TYPES:
            raise ValueError(f"不支持的 action: {action_name}")
        issue = issue_index.get(sample_id)
        if not issue:
            raise ValueError(f"未找到 issue: {sample_id}")
        effective_issue = build_effective_issue_for_action(issue, action)

        if action_name == ACTION_IGNORE:
            update_resolution_state(
                game,
                sample_id=sample_id,
                status=RESOLUTION_IGNORED,
                action=action_name,
                message=resolution_message,
            )
            append_jsonl(
                get_action_log_path(game),
                {
                    "sample_id": sample_id,
                    "action": action_name,
                    "status": RESOLUTION_IGNORED,
                    "message": resolution_message,
                    "updated_at": utc_now_iso(),
                },
            )
            results.append(
                {
                    "sample_id": sample_id,
                    "action": action_name,
                    "status": RESOLUTION_IGNORED,
                    "message": resolution_message,
                }
            )
            continue

        grouped_mutations.setdefault(issue["source_file"], []).append(
            {
                "issue": effective_issue,
                "action": action_name,
                "message": resolution_message,
            }
        )

    for source_file, mutations in grouped_mutations.items():
        source_path = PROJECT_DIR / source_file
        if not source_path.exists():
            for mutation in mutations:
                issue = mutation["issue"]
                update_resolution_state(
                    game,
                    sample_id=issue["sample_id"],
                    status=RESOLUTION_CONFLICT,
                    action=mutation["action"],
                    message="源文件不存在",
                )
                results.append(
                    {
                        "sample_id": issue["sample_id"],
                        "action": mutation["action"],
                        "status": RESOLUTION_CONFLICT,
                        "message": "源文件不存在",
                    }
                )
            continue

        rows = load_jsonl(source_path)
        prepared: list[tuple[int, dict, str]] = []

        try:
            for mutation in mutations:
                issue = mutation["issue"]
                current_rows, current_index = find_current_source_row(game=game, issue=issue)
                rows = current_rows
                prepared.append((current_index, issue, mutation["action"]))
        except Exception as exc:
            message = str(exc)
            for mutation in mutations:
                issue = mutation["issue"]
                update_resolution_state(
                    game,
                    sample_id=issue["sample_id"],
                    status=RESOLUTION_CONFLICT,
                    action=mutation["action"],
                    message=message,
                )
                results.append(
                    {
                        "sample_id": issue["sample_id"],
                        "action": mutation["action"],
                        "status": RESOLUTION_CONFLICT,
                        "message": message,
                    }
                )
            continue

        for current_index, issue, action_name in sorted(prepared, key=lambda item: item[0], reverse=True):
            resolution_message = next(
                (
                    mutation.get("message", "")
                    for mutation in mutations
                    if mutation["issue"]["sample_id"] == issue["sample_id"]
                    and mutation["action"] == action_name
                ),
                "",
            )
            current_sample = rows[current_index]
            if action_name == ACTION_DELETE_SAMPLE:
                rows.pop(current_index)
            else:
                try:
                    updated_sample = apply_expected_to_sample(current_sample, issue, slot_map)
                    target_file = resolve_apply_target_file(game, issue, updated_sample)
                except Exception as exc:
                    message = str(exc)
                    update_resolution_state(
                        game,
                        sample_id=issue["sample_id"],
                        status=RESOLUTION_CONFLICT,
                        action=action_name,
                        message=message,
                    )
                    results.append(
                        {
                            "sample_id": issue["sample_id"],
                            "action": action_name,
                            "status": RESOLUTION_CONFLICT,
                            "message": message,
                        }
                    )
                    continue

                if target_file == issue["source_file"]:
                    rows[current_index] = updated_sample
                else:
                    rows.pop(current_index)
                    pending_appends.setdefault(target_file, []).append(updated_sample)
                    target_command_id = normalize_command_id(updated_sample.get("command_id"))
                    if target_command_id:
                        affected_commands.add(target_command_id)
                if issue.get("source_type") == "global_negative":
                    affected_global_negative = True

            update_resolution_state(
                game,
                sample_id=issue["sample_id"],
                status=RESOLUTION_APPLIED,
                action=action_name,
                message=resolution_message,
            )
            append_jsonl(
                get_action_log_path(game),
                {
                    "sample_id": issue["sample_id"],
                    "action": action_name,
                    "status": RESOLUTION_APPLIED,
                    "source_file": issue["source_file"],
                    "message": resolution_message,
                    "expected_state": describe_expected_state(issue) if action_name == ACTION_APPLY_EXPECTED else "",
                    "updated_at": utc_now_iso(),
                },
            )
            results.append(
                {
                    "sample_id": issue["sample_id"],
                    "action": action_name,
                    "status": RESOLUTION_APPLIED,
                    "message": resolution_message,
                }
            )
            source_command_id = issue.get("source_command_id")
            if source_command_id:
                affected_commands.add(source_command_id)
            elif Path(issue["source_file"]).parent.name:
                parent_name = Path(issue["source_file"]).parent.name
                if parent_name not in {CHECK_DIR_NAME, "quality_audit"}:
                    affected_commands.add(parent_name)

        write_jsonl(source_path, rows)

    for target_file, appended_rows in pending_appends.items():
        target_path = PROJECT_DIR / target_file
        existing_rows = load_jsonl(target_path) if target_path.exists() else []
        existing_rows.extend(appended_rows)
        write_jsonl(target_path, existing_rows)

    if affected_commands or affected_global_negative or any(item["action"] == ACTION_DELETE_SAMPLE for item in results):
        rebuild_command_outputs(game, affected_commands if affected_commands else None)

    summary = build_summary_payload(game)
    return {
        "status": "ok" if all(item["status"] != RESOLUTION_CONFLICT for item in results) else "partial",
        "results": results,
        "summary": summary,
    }


def run_full_data_check(
    *,
    game: str = "mmorpg",
    model: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    restart: bool = False,
    think_mode: bool = False,
    think_level: str = "high",
) -> dict:
    print(f"\n{'=' * 60}")
    print(f"  Step 1: 构建全部数据检查快照 ({game})")
    print(f"{'=' * 60}")

    manifest, snapshot_rows = prepare_full_check_run(
        game=game,
        model=model,
        think_mode=think_mode,
        think_level=think_level,
        batch_size=batch_size,
        restart=restart,
    )

    total_samples = len(snapshot_rows)
    if total_samples == 0:
        manifest["status"] = "empty"
        write_manifest(game, manifest)
        return build_summary_payload(game)

    print(f"  数据集样本数: {total_samples}")
    print(f"  批次大小: {batch_size}")
    print(f"  结果目录: {get_full_check_dir(game)}")
    if load_manifest(game) and not restart:
        print("  恢复策略: 继续未完成 / 失败批次")

    commands_path = PROJECT_DIR / "commands" / f"{game}.json"
    if not commands_path.exists():
        raise FileNotFoundError(f"Commands 文件不存在: {commands_path}")

    command_registry = compact_file(str(commands_path))
    commands = load_commands(str(commands_path))
    game_background = discover_game_background(game)
    optional_stats = build_optional_stats([row["sample"] for row in snapshot_rows], commands)
    slot_map = command_slot_map(game)

    batches = chunk_rows(snapshot_rows, batch_size)
    batch_count = len(batches)

    print(f"\n{'=' * 60}")
    print("  Step 2: 全量检查批次")
    print(f"{'=' * 60}")
    print(f"  批次总数: {batch_count}")

    manifest["status"] = "running"
    write_manifest(game, manifest)

    for batch_index, batch_rows in enumerate(batches, start=1):
        batch_path = get_batch_path(game, batch_index)
        existing_payload = read_json_file(batch_path, default=None)
        if isinstance(existing_payload, dict) and existing_payload.get("status") == "completed":
            print(f"  [SKIP] Batch {batch_index}/{batch_count}: 已完成")
            continue

        print(
            f"\n  Batch {batch_index}/{batch_count}: "
            f"样本 {batch_rows[0]['dataset_index']} - {batch_rows[-1]['dataset_index']}"
        )
        write_json_file(
            batch_path,
            {
                "status": "running",
                "batch_index": batch_index,
                "sample_count": len(batch_rows),
                "range_start": batch_rows[0]["dataset_index"],
                "range_end": batch_rows[-1]["dataset_index"],
                "updated_at": utc_now_iso(),
            },
        )

        batch_context = build_full_check_batch_context(
            game=game,
            input_path=str(PROJECT_DIR / manifest["merged_all_path"]),
            batch_index=batch_index,
            batch_count=batch_count,
            batch_rows=batch_rows,
        )
        prompt = build_full_check_prompt(
            game_background=game_background,
            command_registry=command_registry,
            sample_batch=[row["sample"] for row in batch_rows],
            batch_context=batch_context,
            optional_stats=optional_stats,
        )

        try:
            raw_result = call_llm_json(
                prompt,
                model=model,
                temperature=0.2,
                max_tokens=32000,
                think_mode=think_mode,
                think_level=think_level,
            )
            if not isinstance(raw_result, dict):
                raise ValueError("全部数据检查返回不是 JSON 对象")

            audit_summary = raw_result.get("audit_summary", {})
            if not isinstance(audit_summary, dict):
                audit_summary = {}

            enriched_results = enrich_batch_sample_results(
                raw_results=raw_result.get("sample_results", []),
                batch_rows=batch_rows,
                slot_map=slot_map,
            )
            payload = {
                "status": "completed",
                "batch_index": batch_index,
                "sample_count": len(batch_rows),
                "range_start": batch_rows[0]["dataset_index"],
                "range_end": batch_rows[-1]["dataset_index"],
                "updated_at": utc_now_iso(),
                "audit_summary": audit_summary,
                "systemic_findings": raw_result.get("systemic_findings", []),
                "blind_spots": raw_result.get("blind_spots", []),
                "sample_results": enriched_results,
            }
            write_json_file(batch_path, payload)
            print(
                f"    [OK] verdict={audit_summary.get('final_verdict', 'unknown')} "
                f"risk={audit_summary.get('overall_risk', 'unknown')} "
                f"issues={batch_issue_count(enriched_results)}"
            )
        except Exception as exc:
            write_json_file(
                batch_path,
                {
                    "status": "error",
                    "batch_index": batch_index,
                    "sample_count": len(batch_rows),
                    "range_start": batch_rows[0]["dataset_index"],
                    "range_end": batch_rows[-1]["dataset_index"],
                    "updated_at": utc_now_iso(),
                    "error": str(exc),
                },
            )
            print(f"    [ERROR] Batch {batch_index}/{batch_count}: {exc}")
        finally:
            build_summary_payload(game)

    print(f"\n{'=' * 60}")
    print("  Step 3: 汇总结果")
    print(f"{'=' * 60}")
    summary = build_summary_payload(game)
    print(
        f"  [OK] 批次: {summary['completed_batches']}/{summary['batch_count']} "
        f"| 失败批次: {summary['failed_batches']} "
        f"| 问题样本: {summary['total_issues']}"
    )
    print(f"  [OK] 摘要已保存到: {get_summary_path(game)}")
    if summary["failed_batches"] > 0:
        print("  [WARN] 存在失败批次，可稍后继续未完成 / 失败批次")
    return summary


def build_full_check_issue_review_prompt(
    *,
    game_background: str,
    command_registry: str,
    source_sample: dict,
    issue: dict,
) -> str:
    template = load_issue_review_prompt_template()
    prompt = template.replace("{{game_background}}", game_background)
    prompt = prompt.replace("{{command_registry}}", command_registry)
    prompt = prompt.replace("{{source_sample_json}}", json.dumps(source_sample, ensure_ascii=False, indent=2))
    prompt = prompt.replace("{{issue_json}}", json.dumps(issue, ensure_ascii=False, indent=2))
    return prompt


def build_issue_review_manifest(
    *,
    game: str,
    primary_model: str,
    secondary_model: str,
    think_mode: bool,
    think_level: str,
    full_check_manifest: dict,
) -> dict:
    return {
        "version": ISSUE_REVIEW_VERSION,
        "task_type": "full_check_issue_review",
        "status": "running",
        "game": game,
        "model": primary_model,
        "primary_model": primary_model,
        "secondary_model": secondary_model,
        "consensus_mode": REVIEW_CONSENSUS_MODE_STRICT_STRUCTURED_ACTION,
        "think_mode": think_mode,
        "think_level": think_level,
        "dataset_fingerprint": full_check_manifest.get("dataset_fingerprint"),
        "full_check_prompt_hash": full_check_manifest.get("prompt_hash"),
        "full_check_total_issues": (read_json_file(get_summary_path(game), default={}) or {}).get("total_issues", 0),
        "review_prompt_hash": sha1_text(load_issue_review_prompt_template()),
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def validate_issue_review_resume(
    manifest: dict,
    *,
    full_check_manifest: dict,
    primary_model: str,
    secondary_model: str,
    think_mode: bool,
    think_level: str,
) -> None:
    if manifest.get("dataset_fingerprint") != full_check_manifest.get("dataset_fingerprint"):
        raise ValueError("全部数据检查数据集已变化，不能继续旧的 issue review，请勾选重新开始。")
    if manifest.get("full_check_prompt_hash") != full_check_manifest.get("prompt_hash"):
        raise ValueError("全部数据检查提示词版本已变化，不能继续旧的 issue review，请勾选重新开始。")
    if manifest.get("model") != model:
        raise ValueError("model 与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if bool(manifest.get("think_mode")) != bool(think_mode):
        raise ValueError("think_mode 与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if manifest.get("think_level") != think_level:
        raise ValueError("think_level 与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if manifest.get("review_prompt_hash") != sha1_text(load_issue_review_prompt_template()):
        raise ValueError("单样本复核提示词版本已变化，不能续跑，请勾选重新开始。")


def prepare_full_check_issue_review_run(
    *,
    game: str,
    model: str | None,
    think_mode: bool,
    think_level: str,
    restart: bool,
) -> dict:
    full_check_manifest = load_manifest(game)
    if not isinstance(full_check_manifest, dict):
        raise FileNotFoundError("未找到全部数据检查结果，请先执行全部数据检查。")

    review_dir = get_issue_review_dir(game)
    if restart and review_dir.exists():
        shutil.rmtree(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_issue_review_manifest(game)
    if manifest:
        validate_issue_review_resume(
            manifest,
            full_check_manifest=full_check_manifest,
            model=model,
            think_mode=think_mode,
            think_level=think_level,
        )
        return manifest

    manifest = build_issue_review_manifest(
        game=game,
        model=model,
        think_mode=think_mode,
        think_level=think_level,
        full_check_manifest=full_check_manifest,
    )
    write_issue_review_manifest(game, manifest)
    return manifest


def resolve_issue_review_models(
    *,
    primary_model: str | None,
    secondary_model: str | None,
) -> tuple[str, str]:
    available_models = get_available_models()
    resolved_primary = (primary_model or "").strip()
    resolved_secondary = (secondary_model or "").strip()

    if not resolved_primary:
        raise ValueError("问题样本复核必须指定主模型。")
    if not resolved_secondary:
        raise ValueError("问题样本复核必须指定副模型。")
    if resolved_primary == resolved_secondary:
        raise ValueError("问题样本复核要求主模型和副模型不同。")
    if available_models:
        if resolved_primary not in available_models:
            raise ValueError(f"主模型不在可用列表中: {resolved_primary}")
        if resolved_secondary not in available_models:
            raise ValueError(f"副模型不在可用列表中: {resolved_secondary}")
    return resolved_primary, resolved_secondary


def validate_issue_review_resume_dual_model(
    manifest: dict,
    *,
    full_check_manifest: dict,
    primary_model: str,
    secondary_model: str,
    think_mode: bool,
    think_level: str,
) -> None:
    if int(manifest.get("version") or 0) < ISSUE_REVIEW_VERSION:
        raise ValueError("现有 issue review 为旧版单模型工件，不能续跑，请勾选重新开始。")
    if manifest.get("dataset_fingerprint") != full_check_manifest.get("dataset_fingerprint"):
        raise ValueError("全部数据检查数据集已变化，不能继续旧的 issue review，请勾选重新开始。")
    if manifest.get("full_check_prompt_hash") != full_check_manifest.get("prompt_hash"):
        raise ValueError("全部数据检查提示词版本已变化，不能继续旧的 issue review，请勾选重新开始。")
    if manifest.get("model") != primary_model or manifest.get("primary_model") != primary_model:
        raise ValueError("主模型与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if manifest.get("secondary_model") != secondary_model:
        raise ValueError("副模型与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if manifest.get("consensus_mode") != REVIEW_CONSENSUS_MODE_STRICT_STRUCTURED_ACTION:
        raise ValueError("issue review 共识模式已变化，不能续跑，请勾选重新开始。")
    if bool(manifest.get("think_mode")) != bool(think_mode):
        raise ValueError("think_mode 与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if manifest.get("think_level") != think_level:
        raise ValueError("think_level 与现有 issue review 不一致，不能续跑，请勾选重新开始。")
    if manifest.get("review_prompt_hash") != sha1_text(load_issue_review_prompt_template()):
        raise ValueError("单样本复核提示词版本已变化，不能续跑，请勾选重新开始。")


def prepare_full_check_issue_review_run_dual_model(
    *,
    game: str,
    primary_model: str,
    secondary_model: str,
    think_mode: bool,
    think_level: str,
    restart: bool,
) -> dict:
    full_check_manifest = load_manifest(game)
    if not isinstance(full_check_manifest, dict):
        raise FileNotFoundError("未找到全部数据检查结果，请先执行全部数据检查。")

    review_dir = get_issue_review_dir(game)
    if restart and review_dir.exists():
        shutil.rmtree(review_dir)
    review_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_issue_review_manifest(game)
    if manifest:
        validate_issue_review_resume_dual_model(
            manifest,
            full_check_manifest=full_check_manifest,
            primary_model=primary_model,
            secondary_model=secondary_model,
            think_mode=think_mode,
            think_level=think_level,
        )
        return manifest

    manifest = build_issue_review_manifest(
        game=game,
        primary_model=primary_model,
        secondary_model=secondary_model,
        think_mode=think_mode,
        think_level=think_level,
        full_check_manifest=full_check_manifest,
    )
    write_issue_review_manifest(game, manifest)
    return manifest


def normalize_issue_review_result(issue: dict, raw_result: dict) -> dict:
    decision = str(raw_result.get("decision") or "").strip()
    if decision not in REVIEW_DECISIONS:
        decision = REVIEW_DECISION_PENDING_CONFIRMATION

    action = str(raw_result.get("action") or REVIEW_ACTION_NONE).strip()
    if action not in REVIEW_ACTIONS:
        action = REVIEW_ACTION_NONE

    confidence = str(raw_result.get("confidence") or "low").strip().lower()
    if confidence not in REVIEW_CONFIDENCE_LEVELS:
        confidence = "low"

    final_label = str(raw_result.get("final_label") or "").strip() or None
    if final_label not in VALID_SAMPLE_LABELS:
        final_label = None

    final_command_id = normalize_command_id(raw_result.get("final_command_id")) or None
    final_slots = normalize_slots(raw_result.get("final_slots"))
    final_bucket = normalize_bucket(raw_result.get("final_bucket")) or None
    reason = str(raw_result.get("reason") or "").strip()
    pending_reason = str(raw_result.get("pending_reason") or "").strip()
    raw_notes = raw_result.get("safety_notes")
    safety_notes = [
        str(item).strip()
        for item in (raw_notes if isinstance(raw_notes, list) else [])
        if str(item).strip()
    ]

    if action == REVIEW_ACTION_NONE and decision == REVIEW_DECISION_AUTO_PROCESS:
        decision = REVIEW_DECISION_PENDING_CONFIRMATION
        pending_reason = pending_reason or "未提供可自动执行的动作。"

    if decision == REVIEW_DECISION_AUTO_PROCESS and confidence != "high":
        decision = REVIEW_DECISION_PENDING_CONFIRMATION
        pending_reason = pending_reason or "置信度不足，不能自动处理。"
        action = REVIEW_ACTION_NONE

    if action == ACTION_DELETE_SAMPLE:
        final_label = None
        final_command_id = None
        final_slots = {}
        final_bucket = None

    if action == ACTION_IGNORE:
        final_label = final_label or issue.get("current_label")
        final_command_id = final_command_id or normalize_command_id(issue.get("current_command_id")) or None
        final_slots = final_slots or normalize_slots(issue.get("current_slots"))
        final_bucket = final_bucket or normalize_bucket(issue.get("current_bucket")) or None

    if final_label != "quick_command":
        final_command_id = None
        final_slots = {}

    if issue.get("source_type") != "global_negative":
        final_bucket = None

    return {
        "decision": decision,
        "action": action,
        "confidence": confidence,
        "final_label": final_label,
        "final_command_id": final_command_id,
        "final_slots": final_slots,
        "final_bucket": final_bucket,
        "reason": reason,
        "pending_reason": pending_reason,
        "safety_notes": safety_notes,
    }


def review_matches_current_state(issue: dict, review_result: dict) -> bool:
    return (
        review_result.get("final_label") == issue.get("current_label")
        and normalize_command_id(review_result.get("final_command_id")) == normalize_command_id(issue.get("current_command_id"))
        and normalize_slots(review_result.get("final_slots")) == normalize_slots(issue.get("current_slots"))
        and normalize_bucket(review_result.get("final_bucket")) == normalize_bucket(issue.get("current_bucket"))
    )


def build_auto_action_from_review(issue: dict, review_result: dict) -> tuple[dict | None, str]:
    if review_result["decision"] != REVIEW_DECISION_AUTO_PROCESS:
        return None, review_result.get("pending_reason") or "该样本需要人工确认。"

    action = review_result["action"]
    reason = review_result.get("reason") or ""
    source_type = str(issue.get("source_type") or "")

    if action == ACTION_IGNORE:
        if not review_matches_current_state(issue, review_result):
            return None, "复核结果选择 ignore，但最终状态与当前样本不一致。"
        return {
            "sample_id": issue["sample_id"],
            "action": ACTION_IGNORE,
            "resolution_message": reason,
        }, ""

    if action == ACTION_DELETE_SAMPLE:
        return {
            "sample_id": issue["sample_id"],
            "action": ACTION_DELETE_SAMPLE,
            "resolution_message": reason,
        }, ""

    if action != ACTION_APPLY_EXPECTED:
        return None, "未提供受支持的自动处理动作。"

    final_label = review_result.get("final_label")
    if final_label not in VALID_SAMPLE_LABELS:
        return None, "apply_expected 缺少有效的 final_label。"

    if review_matches_current_state(issue, review_result):
        return None, "apply_expected 未产生任何实际改动。"

    payload = {
        "sample_id": issue["sample_id"],
        "action": ACTION_APPLY_EXPECTED,
        "expected_label": final_label,
        "resolution_message": reason,
    }

    if source_type == "global_negative":
        if final_label == "quick_command":
            return None, "global_negative 判为 quick_command 时不能自动迁移，需人工确认。"
        final_bucket = normalize_bucket(review_result.get("final_bucket"))
        if not final_bucket:
            return None, "global_negative 缺少 final_bucket，不能自动处理。"
        try:
            validate_global_negative_bucket(final_label, final_bucket)
        except ValueError as exc:
            return None, str(exc)
        payload["expected_bucket"] = final_bucket
        return payload, ""

    if final_label == "quick_command":
        expected_command_id = normalize_command_id(review_result.get("final_command_id"))
        expected_slots = normalize_slots(review_result.get("final_slots"))
        if not expected_command_id:
            return None, "quick_command 缺少 final_command_id。"
        if not expected_slots:
            return None, "quick_command 缺少 final_slots。"
        payload["expected_command_id"] = expected_command_id
        payload["expected_slots"] = expected_slots

    return payload, ""


def build_pending_review_result(reason: str) -> dict:
    return {
        "decision": REVIEW_DECISION_PENDING_CONFIRMATION,
        "action": REVIEW_ACTION_NONE,
        "confidence": "low",
        "final_label": None,
        "final_command_id": None,
        "final_slots": {},
        "final_bucket": None,
        "reason": "",
        "pending_reason": reason,
        "safety_notes": [],
    }


def normalize_review_action_payload(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    action = str(payload.get("action") or "").strip()
    if action not in ACTION_TYPES:
        return None

    normalized = {
        "action": action,
        "expected_label": None,
        "expected_command_id": None,
        "expected_slots": {},
        "expected_bucket": None,
    }
    if action != ACTION_APPLY_EXPECTED:
        return normalized

    expected_label = str(payload.get("expected_label") or "").strip() or None
    if expected_label not in VALID_SAMPLE_LABELS:
        return None
    normalized["expected_label"] = expected_label

    if expected_label == "quick_command":
        normalized["expected_command_id"] = normalize_command_id(payload.get("expected_command_id")) or None
        normalized["expected_slots"] = normalize_slots(payload.get("expected_slots"))
    normalized["expected_bucket"] = normalize_bucket(payload.get("expected_bucket")) or None
    return normalized


def compare_normalized_review_actions(
    left: dict | None,
    right: dict | None,
) -> list[str]:
    if left is None or right is None:
        if left == right:
            return []
        return ["decision", "action"]

    mismatches: list[str] = []
    for key in ("action", "expected_label", "expected_command_id", "expected_slots", "expected_bucket"):
        if left.get(key) != right.get(key):
            mismatches.append(key)
    return mismatches


def confidence_to_rank(confidence: str | None) -> int:
    return REVIEW_CONFIDENCE_RANKS.get(str(confidence or "").strip().lower(), 0)


def rank_to_confidence(rank: int) -> str:
    if rank >= REVIEW_CONFIDENCE_RANKS["high"]:
        return "high"
    if rank >= REVIEW_CONFIDENCE_RANKS["medium"]:
        return "medium"
    return "low"


def merge_pending_confidence(primary_review: dict | None, secondary_review: dict | None) -> str:
    ranks = [
        confidence_to_rank((primary_review or {}).get("confidence")),
        confidence_to_rank((secondary_review or {}).get("confidence")),
    ]
    return rank_to_confidence(min(ranks))


def build_review_detail_payload(
    *,
    model: str,
    review_result: dict,
    demotion_reason: str = "",
    error_message: str = "",
) -> dict:
    return {
        "model": model,
        "decision": review_result.get("decision"),
        "action": review_result.get("action"),
        "confidence": review_result.get("confidence"),
        "final_label": review_result.get("final_label"),
        "final_command_id": review_result.get("final_command_id"),
        "final_slots": normalize_slots(review_result.get("final_slots")),
        "final_bucket": normalize_bucket(review_result.get("final_bucket")) or None,
        "reason": review_result.get("reason", ""),
        "pending_reason": review_result.get("pending_reason", ""),
        "safety_notes": review_result.get("safety_notes", []),
        "demotion_reason": demotion_reason,
        "error_message": error_message,
    }


def evaluate_single_issue_review(
    *,
    issue: dict,
    prompt: str,
    model: str,
    think_mode: bool,
    think_level: str,
) -> dict:
    demotion_reason = ""
    error_message = ""
    try:
        raw_result = call_llm_json(
            prompt,
            model=model,
            temperature=0.2,
            max_tokens=12000,
            think_mode=think_mode,
            think_level=think_level,
        )
        if not isinstance(raw_result, dict):
            raise ValueError("issue review 返回不是 JSON 对象")
        review_result = normalize_issue_review_result(issue, raw_result)
    except Exception as exc:
        error_message = f"LLM 复核失败: {exc}"
        review_result = build_pending_review_result(error_message)

    action_payload, demotion_reason = build_auto_action_from_review(issue, review_result)
    if not action_payload:
        review_result["decision"] = REVIEW_DECISION_PENDING_CONFIRMATION
        review_result["action"] = REVIEW_ACTION_NONE
        review_result["pending_reason"] = review_result.get("pending_reason") or demotion_reason or "需要人工确认。"

    return {
        "model": model,
        "review_result": review_result,
        "action_payload": action_payload,
        "normalized_action": normalize_review_action_payload(action_payload),
        "demotion_reason": demotion_reason,
        "error_message": error_message,
        "detail": build_review_detail_payload(
            model=model,
            review_result=review_result,
            demotion_reason=demotion_reason,
            error_message=error_message,
        ),
    }


def build_consensus_reason(primary_review: dict, secondary_review: dict) -> str:
    primary_reason = str(primary_review.get("reason") or "").strip()
    secondary_reason = str(secondary_review.get("reason") or "").strip()
    if primary_reason and secondary_reason and primary_reason != secondary_reason:
        return f"双模型结构化自动动作一致。{primary_review.get('model')}: {primary_reason} | {secondary_review.get('model')}: {secondary_reason}"
    if primary_reason:
        return f"双模型结构化自动动作一致。{primary_reason}"
    if secondary_reason:
        return f"双模型结构化自动动作一致。{secondary_reason}"
    return "双模型结构化自动动作一致。"


def build_pending_consensus_reason(
    *,
    agreement_status: str,
    primary_review: dict | None,
    secondary_review: dict | None,
    disagreement_fields: list[str],
) -> str:
    if agreement_status == REVIEW_AGREEMENT_BOTH_ERROR:
        return "双模型复核均失败，不能自动处理。"
    if agreement_status == REVIEW_AGREEMENT_PRIMARY_ERROR:
        return "主模型复核失败，不能自动处理。"
    if agreement_status == REVIEW_AGREEMENT_SECONDARY_ERROR:
        return "副模型复核失败，不能自动处理。"
    if agreement_status == REVIEW_AGREEMENT_MISMATCH:
        fields = ", ".join(disagreement_fields) or "action"
        return f"双模型复核结论不一致，不能自动处理。冲突字段: {fields}"
    if primary_review is None and secondary_review is None:
        return "缺少可安全复核的数据集快照，不能自动处理。"
    return "双模型均判定该样本仍需人工确认。"


def build_consensus_review_result(
    *,
    issue: dict,
    primary_eval: dict | None,
    secondary_eval: dict | None,
) -> tuple[dict, dict | None]:
    primary_review = primary_eval["review_result"] if primary_eval else None
    secondary_review = secondary_eval["review_result"] if secondary_eval else None
    primary_error = str((primary_eval or {}).get("error_message") or "").strip()
    secondary_error = str((secondary_eval or {}).get("error_message") or "").strip()

    if primary_error and secondary_error:
        agreement_status = REVIEW_AGREEMENT_BOTH_ERROR
    elif primary_error:
        agreement_status = REVIEW_AGREEMENT_PRIMARY_ERROR
    elif secondary_error:
        agreement_status = REVIEW_AGREEMENT_SECONDARY_ERROR
    else:
        primary_action = (primary_eval or {}).get("normalized_action")
        secondary_action = (secondary_eval or {}).get("normalized_action")
        if primary_action and secondary_action:
            disagreement_fields = compare_normalized_review_actions(primary_action, secondary_action)
            if not disagreement_fields:
                consensus_result = dict(primary_review or {})
                consensus_result["decision"] = REVIEW_DECISION_AUTO_PROCESS
                consensus_result["action"] = primary_action["action"]
                consensus_result["confidence"] = "high"
                consensus_result["reason"] = build_consensus_reason(
                    primary_eval["detail"],
                    secondary_eval["detail"],
                )
                consensus_result["pending_reason"] = ""
                return {
                    **consensus_result,
                    "agreement_status": REVIEW_AGREEMENT_AGREED_AUTO_PROCESS,
                    "disagreement_fields": [],
                }, dict((primary_eval or {}).get("action_payload") or {})
            agreement_status = REVIEW_AGREEMENT_MISMATCH
        elif primary_action or secondary_action:
            agreement_status = REVIEW_AGREEMENT_MISMATCH
            disagreement_fields = ["decision", "action"]
        else:
            agreement_status = REVIEW_AGREEMENT_AGREED_PENDING_CONFIRMATION
            disagreement_fields = []

        if agreement_status == REVIEW_AGREEMENT_AGREED_PENDING_CONFIRMATION:
            return {
                **build_pending_review_result(
                    build_pending_consensus_reason(
                        agreement_status=agreement_status,
                        primary_review=(primary_eval or {}).get("detail"),
                        secondary_review=(secondary_eval or {}).get("detail"),
                        disagreement_fields=[],
                    )
                ),
                "confidence": merge_pending_confidence(primary_review, secondary_review),
                "agreement_status": agreement_status,
                "disagreement_fields": [],
            }, None

        return {
            **build_pending_review_result(
                build_pending_consensus_reason(
                    agreement_status=agreement_status,
                    primary_review=(primary_eval or {}).get("detail"),
                    secondary_review=(secondary_eval or {}).get("detail"),
                    disagreement_fields=disagreement_fields,
                )
            ),
            "agreement_status": agreement_status,
            "disagreement_fields": disagreement_fields,
        }, None

    return {
        **build_pending_review_result(
            build_pending_consensus_reason(
                agreement_status=agreement_status,
                primary_review=(primary_eval or {}).get("detail"),
                secondary_review=(secondary_eval or {}).get("detail"),
                disagreement_fields=[],
            )
        ),
        "agreement_status": agreement_status,
        "disagreement_fields": [],
    }, None


def run_full_check_issue_review(
    *,
    game: str = "mmorpg",
    model: str | None = None,
    secondary_model: str | None = None,
    restart: bool = False,
    think_mode: bool = False,
    think_level: str = "high",
    limit: int | None = None,
) -> dict:
    primary_model, secondary_model = resolve_issue_review_models(
        primary_model=model,
        secondary_model=secondary_model,
    )

    print(f"\n{'=' * 60}")
    print(f"  Step 1: 准备 issue review ({game})")
    print(f"{'=' * 60}")

    manifest = prepare_full_check_issue_review_run_dual_model(
        game=game,
        primary_model=primary_model,
        secondary_model=secondary_model,
        think_mode=think_mode,
        think_level=think_level,
        restart=restart,
    )
    manifest["status"] = "running"
    write_issue_review_manifest(game, manifest)

    commands_path = PROJECT_DIR / "commands" / f"{game}.json"
    if not commands_path.exists():
        raise FileNotFoundError(f"Commands 文件不存在: {commands_path}")

    issue_index = build_issue_index(game)
    resolution_state = load_resolution_state(game)
    review_state = load_issue_review_map(game)
    snapshot_rows = {
        row.get("sample_id"): row
        for row in load_snapshot_rows(game)
        if isinstance(row, dict) and row.get("sample_id")
    }
    pending_issues = [
        merge_issue_resolution_state(issue, resolution_state)
        for issue in issue_index.values()
        if resolution_state.get(issue["sample_id"], {}).get("status", RESOLUTION_PENDING) in {RESOLUTION_PENDING, RESOLUTION_CONFLICT}
        and issue["sample_id"] not in review_state
    ]
    pending_issues.sort(key=lambda item: (item.get("dataset_index", 0), item.get("batch_index", 0)))
    if limit is not None and limit > 0:
        pending_issues = pending_issues[:limit]

    summary = build_issue_review_summary_payload(game)
    print(f"  待复核 issue: {len(pending_issues)}")
    print(f"  结果目录: {get_issue_review_dir(game)}")
    print(f"  模型对: {primary_model} x {secondary_model}")
    if summary:
        print(f"  已有复核记录: {summary.get('reviewed_total', 0)}")

    if not pending_issues:
        manifest["status"] = "completed"
        write_issue_review_manifest(game, manifest)
        final_summary = build_issue_review_summary_payload(game)
        return {
            "status": "completed",
            "summary": final_summary,
            "processed": 0,
        }

    command_registry = compact_file(str(commands_path))
    game_background = discover_game_background(game)

    print(f"\n{'=' * 60}")
    print("  Step 2: 逐条复核并自动处理")
    print(f"{'=' * 60}")

    processed = 0
    for index, issue in enumerate(pending_issues, start=1):
        sample_id = issue["sample_id"]
        snapshot_row = snapshot_rows.get(sample_id)
        if not isinstance(snapshot_row, dict):
            review_entry = {
                "sample_id": sample_id,
                "dataset_index": issue.get("dataset_index"),
                "source_type": issue.get("source_type"),
                **build_pending_review_result("未找到对应的数据集快照，无法安全复核。"),
                "primary_model": primary_model,
                "secondary_model": secondary_model,
                "primary_review": None,
                "secondary_review": None,
                "agreement_status": REVIEW_AGREEMENT_AGREED_PENDING_CONFIRMATION,
                "disagreement_fields": [],
                "execution_status": REVIEW_DECISION_PENDING_CONFIRMATION,
                "execution_message": "",
                "updated_at": utc_now_iso(),
            }
            append_jsonl(get_issue_review_log_path(game), review_entry)
            build_issue_review_summary_payload(game)
            print(f"  [{index}/{len(pending_issues)}] #{issue.get('dataset_index', '-')} 待确认: 缺少快照")
            processed += 1
            continue

        source_sample = snapshot_row.get("sample")
        prompt = build_full_check_issue_review_prompt(
            game_background=game_background,
            command_registry=command_registry,
            source_sample=source_sample,
            issue=issue,
        )

        primary_eval = evaluate_single_issue_review(
            issue=issue,
            prompt=prompt,
            model=primary_model,
            think_mode=think_mode,
            think_level=think_level,
        )
        secondary_eval = evaluate_single_issue_review(
            issue=issue,
            prompt=prompt,
            model=secondary_model,
            think_mode=think_mode,
            think_level=think_level,
        )
        review_result, action_payload = build_consensus_review_result(
            issue=issue,
            primary_eval=primary_eval,
            secondary_eval=secondary_eval,
        )

        review_entry = {
            "sample_id": sample_id,
            "dataset_index": issue.get("dataset_index"),
            "source_type": issue.get("source_type"),
            **review_result,
            "primary_model": primary_model,
            "secondary_model": secondary_model,
            "primary_review": primary_eval["detail"],
            "secondary_review": secondary_eval["detail"],
            "execution_status": REVIEW_DECISION_PENDING_CONFIRMATION,
            "execution_message": "",
            "updated_at": utc_now_iso(),
        }

        if action_payload:
            action_payload = dict(action_payload)
            action_payload["resolution_message"] = review_result.get("reason") or action_payload.get("resolution_message", "")
            action_result = apply_full_check_actions(game, [action_payload])
            result_items = action_result.get("results", [])
            first_result = result_items[0] if result_items else {}
            review_entry["execution_status"] = first_result.get("status", "")
            review_entry["execution_message"] = first_result.get("message", "")
        else:
            review_entry["execution_status"] = REVIEW_DECISION_PENDING_CONFIRMATION

        append_jsonl(get_issue_review_log_path(game), review_entry)
        build_issue_review_summary_payload(game)
        processed += 1

        state_label = review_entry["execution_status"] or review_entry["decision"]
        print(
            f"  [{index}/{len(pending_issues)}] "
            f"#{issue.get('dataset_index', '-')} -> {state_label}"
        )

    print(f"\n{'=' * 60}")
    print("  Step 3: 汇总复核结果")
    print(f"{'=' * 60}")
    final_summary = build_issue_review_summary_payload(game)
    manifest["status"] = "completed"
    write_issue_review_manifest(game, manifest)
    print(
        "  [OK] "
        f"已复核: {final_summary.get('reviewed_total', 0)} "
        f"| 自动处理: {final_summary.get('auto_processed', 0)} "
        f"| 待确认: {final_summary.get('pending_confirmation', 0)} "
        f"| 双模型一致自动: {final_summary.get('dual_model_agree_auto', 0)} "
        f"| 双模型冲突: {final_summary.get('dual_model_disagree', 0)} "
        f"| 冲突: {final_summary.get('apply_conflicts', 0)}"
    )
    print(f"  [OK] 摘要已保存到: {get_issue_review_summary_path(game)}")
    return {
        "status": "completed",
        "summary": final_summary,
        "processed": processed,
    }


def main() -> None:
    args = parse_args()
    run_full_data_check(
        game=args.game,
        model=args.model,
        batch_size=args.batch_size,
        restart=args.restart,
        think_mode=args.think_mode,
        think_level=args.think_level,
    )


if __name__ == "__main__":
    main()
