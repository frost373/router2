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
from llm_client import call_llm_json

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "output"

DEFAULT_BATCH_SIZE = 20
CHECK_DIR_NAME = "full_data_check"
CHECK_VERSION = 1

MANIFEST_FILE = "manifest.json"
SUMMARY_FILE = "summary.json"
SNAPSHOT_FILE = "dataset_snapshot.jsonl"
RESOLUTION_STATE_FILE = "resolution_state.json"
ACTION_LOG_FILE = "actions.jsonl"
BATCH_FILE_PATTERN = "batch_{batch_index:04d}.json"

SOURCE_FILE_TYPES: list[tuple[str, str]] = [
    ("template.jsonl", "template"),
    ("adversarial.jsonl", "adversarial"),
    ("paraphrase.jsonl", "paraphrase"),
]
SOURCE_TYPE_TO_FILENAME = {source_type: filename for filename, source_type in SOURCE_FILE_TYPES}

ISSUE_FILTERABLE_VERDICTS = {"pass", "borderline", "fail", "fatal"}
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
        "utterance": sample_result.get("utterance") or get_utterance(current_sample),
        "current_label": sample_result.get("current_label") or normalize_label(current_sample),
        "current_command_id": normalize_command_id(
            sample_result.get("current_command_id") or current_sample.get("command_id")
        ),
        "current_slots": normalize_slots(current_sample.get("slots")),
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


def get_full_check_overview(game: str) -> dict:
    check_dir = get_full_check_dir(game)
    if not check_dir.exists():
        return {
            "exists": False,
            "game": game,
            "output_dir": str(check_dir),
            "manifest": None,
            "summary": None,
            "can_resume": False,
        }

    manifest = load_manifest(game)
    summary = read_json_file(get_summary_path(game), default=None)
    if not summary and manifest:
        summary = build_summary_payload(game)

    can_resume = False
    if isinstance(manifest, dict):
        can_resume = (
            manifest.get("status") in {"running", "completed_with_errors"}
            or manifest.get("pending_batches", 0) > 0
            or manifest.get("failed_batches", 0) > 0
        )

    resolution_state = load_resolution_state(game)
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
    issues = [
        merge_issue_resolution_state(issue, resolution_state)
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


def apply_expected_to_sample(sample: dict, issue: dict) -> dict:
    expected_label = issue.get("expected_label")
    if expected_label not in {"quick_command", "tactical", "chat"}:
        raise ValueError(f"无效的 expected_label: {expected_label}")

    updated = dict(sample)
    updated["label"] = expected_label

    if expected_label == "quick_command":
        expected_command_id = normalize_command_id(issue.get("expected_command_id"))
        if not expected_command_id:
            raise ValueError("缺少 expected_command_id，无法应用建议")
        updated["command_id"] = expected_command_id
        updated["slots"] = normalize_slots(issue.get("expected_slots"))
    else:
        updated["command_id"] = ""
        updated["slots"] = {}

    return updated


def resolve_apply_target_file(game: str, issue: dict, updated_sample: dict) -> str:
    source_type = str(issue.get("source_type") or "")
    expected_label = normalize_label(updated_sample)

    if source_type == "global_negative":
        raise ValueError("global_negative 样本不支持直接应用建议，请删除或忽略")

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

    affected_commands: set[str] = set()
    pending_appends: dict[str, list[dict]] = {}
    results: list[dict] = []

    grouped_mutations: dict[str, list[dict]] = {}
    for action in actions:
        sample_id = str(action.get("sample_id") or "").strip()
        action_name = str(action.get("action") or "").strip()
        if not sample_id:
            raise ValueError("sample_id 不能为空")
        if action_name not in ACTION_TYPES:
            raise ValueError(f"不支持的 action: {action_name}")
        issue = issue_index.get(sample_id)
        if not issue:
            raise ValueError(f"未找到 issue: {sample_id}")

        if action_name == ACTION_IGNORE:
            update_resolution_state(
                game,
                sample_id=sample_id,
                status=RESOLUTION_IGNORED,
                action=action_name,
            )
            append_jsonl(
                get_action_log_path(game),
                {
                    "sample_id": sample_id,
                    "action": action_name,
                    "status": RESOLUTION_IGNORED,
                    "updated_at": utc_now_iso(),
                },
            )
            results.append(
                {
                    "sample_id": sample_id,
                    "action": action_name,
                    "status": RESOLUTION_IGNORED,
                }
            )
            continue

        grouped_mutations.setdefault(issue["source_file"], []).append(
            {
                "issue": issue,
                "action": action_name,
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
            current_sample = rows[current_index]
            if action_name == ACTION_DELETE_SAMPLE:
                rows.pop(current_index)
            else:
                try:
                    updated_sample = apply_expected_to_sample(current_sample, issue)
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

            update_resolution_state(
                game,
                sample_id=issue["sample_id"],
                status=RESOLUTION_APPLIED,
                action=action_name,
            )
            append_jsonl(
                get_action_log_path(game),
                {
                    "sample_id": issue["sample_id"],
                    "action": action_name,
                    "status": RESOLUTION_APPLIED,
                    "source_file": issue["source_file"],
                    "updated_at": utc_now_iso(),
                },
            )
            results.append(
                {
                    "sample_id": issue["sample_id"],
                    "action": action_name,
                    "status": RESOLUTION_APPLIED,
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

    if affected_commands or any(item["action"] == ACTION_DELETE_SAMPLE for item in results):
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
