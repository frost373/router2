#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练数据质量抽查器。

对 merged_all.jsonl 做多轮抽样审计，并将结果保存到 output/{game}/quality_audit/。
"""

import argparse
import json
import os
import random
from collections import Counter
from typing import Any

from command_registry_compact import compact_file, load_commands
from generate_global_negatives import DEFAULT_GAME_BACKGROUND
from llm_client import call_llm_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

DEFAULT_AUDIT_SAMPLE_COUNT = 12
DEFAULT_AUDIT_ROUNDS = 2
DEFAULT_AUDIT_SEED = 42


def parse_args():
    parser = argparse.ArgumentParser(description="训练数据质量抽查器")
    parser.add_argument(
        "--game", default="mmorpg", help="游戏类型（对应 commands/ 下的 JSON 文件名）"
    )
    parser.add_argument(
        "--model", default=None, help="LLM 模型名称（默认使用 LLM.txt 中默认模型）"
    )
    parser.add_argument(
        "--input_path", default=None,
        help="待抽查的 merged_all.jsonl 路径（默认 output/{game}/merged_all.jsonl）",
    )
    parser.add_argument(
        "--sample_count", type=int, default=DEFAULT_AUDIT_SAMPLE_COUNT,
        help=f"单次抽查样本数（默认 {DEFAULT_AUDIT_SAMPLE_COUNT}）",
    )
    parser.add_argument(
        "--rounds", type=int, default=DEFAULT_AUDIT_ROUNDS,
        help=f"抽查轮数（默认 {DEFAULT_AUDIT_ROUNDS}）",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_AUDIT_SEED,
        help=f"抽样随机种子（默认 {DEFAULT_AUDIT_SEED}）",
    )
    parser.add_argument(
        "--think_mode", action="store_true", help="开启思考模式"
    )
    parser.add_argument(
        "--think_level", default="high", help="思考等级（low/medium/high）"
    )
    return parser.parse_args()


def load_prompt_template() -> str:
    """加载质量抽查提示词模板。"""
    path = os.path.join(SCRIPT_DIR, "prompts", "quality_audit_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_jsonl(filepath: str) -> list[dict]:
    """加载 JSONL 文件。"""
    samples: list[dict] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def normalize_label(sample: dict) -> str:
    """兼容字符串/对象两种 label 格式。"""
    label = sample.get("label", "unknown")
    if isinstance(label, dict):
        return label.get("type", "unknown")
    return label or "unknown"


def infer_source_type(sample: dict) -> str:
    """在缺少 source_type 时做兜底推断。"""
    explicit = sample.get("source_type")
    if explicit:
        return explicit
    if sample.get("edit_type") or sample.get("source_command_id"):
        return "adversarial"
    if sample.get("source_text") and normalize_label(sample) == "quick_command":
        return "paraphrase"
    if isinstance(sample.get("meta"), dict) and sample["meta"].get("bucket"):
        return "global_negative"
    if sample.get("command_id") or sample.get("text"):
        return "template"
    return "unknown"


def get_utterance(sample: dict) -> str:
    """统一获取样本文本。"""
    return sample.get("text") or sample.get("input") or ""


def get_bucket(sample: dict) -> str:
    """获取全局负样本 bucket。"""
    meta = sample.get("meta", {})
    if isinstance(meta, dict):
        return meta.get("bucket", "")
    return ""


def build_duplicate_stats(samples: list[dict], top_k: int = 10) -> list[dict]:
    """汇总精确重复文本。"""
    text_counter = Counter(get_utterance(sample) for sample in samples if get_utterance(sample))
    duplicates = [
        {"text": text, "count": count}
        for text, count in text_counter.most_common(top_k)
        if count > 1
    ]
    return duplicates


def build_optional_stats(samples: list[dict], commands: list[dict]) -> dict:
    """构建全量统计，供抽查提示词参考。"""
    label_counts = Counter()
    source_type_counts = Counter()
    bucket_counts = Counter()
    command_counts = Counter()
    source_type_by_label: dict[str, Counter] = {}

    for sample in samples:
        label = normalize_label(sample)
        source_type = infer_source_type(sample)
        bucket = get_bucket(sample)
        command_id = sample.get("command_id") or sample.get("source_command_id") or ""

        label_counts[label] += 1
        source_type_counts[source_type] += 1
        if bucket:
            bucket_counts[bucket] += 1
        if command_id:
            command_counts[command_id] += 1

        source_type_by_label.setdefault(label, Counter())[source_type] += 1

    registry_command_ids = [command["command_id"] for command in commands]
    missing_commands = sorted(
        command_id for command_id in registry_command_ids if command_id not in command_counts
    )

    return {
        "scope": "full_dataset",
        "covers_full_dataset": True,
        "total_samples": len(samples),
        "label_distribution": dict(sorted(label_counts.items())),
        "source_type_distribution": dict(sorted(source_type_counts.items())),
        "bucket_distribution": dict(sorted(bucket_counts.items())),
        "command_sample_counts": dict(sorted(command_counts.items())),
        "registry_command_coverage": {
            "total_commands": len(registry_command_ids),
            "commands_with_samples": len(command_counts),
            "missing_commands": missing_commands,
        },
        "exact_duplicates_top": build_duplicate_stats(samples),
        "source_type_by_label": {
            label: dict(sorted(counter.items()))
            for label, counter in sorted(source_type_by_label.items())
        },
    }


def build_batch_context(
    game: str,
    input_path: str,
    round_index: int,
    rounds: int,
    requested_sample_count: int,
    actual_sample_count: int,
    batch: list[dict],
    seed: int,
) -> dict:
    """构建本轮抽查上下文。"""
    label_counts = Counter(normalize_label(sample) for sample in batch)
    source_type_counts = Counter(infer_source_type(sample) for sample in batch)

    return {
        "game": game,
        "dataset_scope": os.path.basename(input_path),
        "dataset_path": input_path,
        "audit_round_index": round_index,
        "audit_rounds_total": rounds,
        "sample_count_requested": requested_sample_count,
        "sample_count_actual": actual_sample_count,
        "sampling_strategy": "coverage_first_shuffle_without_replacement_until_exhausted",
        "sampling_seed": seed,
        "covers_full_dataset": False,
        "batch_label_distribution": dict(sorted(label_counts.items())),
        "batch_source_type_distribution": dict(sorted(source_type_counts.items())),
    }


def discover_game_background(game: str) -> str:
    """尽量从现有产物中发现游戏背景，没有则回退默认值。"""
    game_dir = os.path.join(PROJECT_DIR, "output", game)

    txt_path = os.path.join(game_dir, "game_background.txt")
    if os.path.isfile(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    json_path = os.path.join(game_dir, "audit_context.json")
    if os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            value = data.get("game_background", "")
            if isinstance(value, str) and value.strip():
                return value.strip()

    if game == "mmorpg":
        return DEFAULT_GAME_BACKGROUND
    return ""


def build_prompt(
    template: str,
    game_background: str,
    command_registry: str,
    sample_batch: list[dict],
    batch_context: dict,
    optional_stats: dict,
) -> str:
    """替换提示词模板中的占位符。"""
    prompt = template.replace("{{game_background}}", game_background)
    prompt = prompt.replace("{{command_registry}}", command_registry)
    prompt = prompt.replace(
        "{{sample_batch_json}}",
        json.dumps(sample_batch, ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace(
        "{{batch_context_json}}",
        json.dumps(batch_context, ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace(
        "{{optional_stats_json}}",
        json.dumps(optional_stats, ensure_ascii=False, indent=2),
    )
    return prompt


def create_round_batches(
    samples: list[dict],
    sample_count: int,
    rounds: int,
    seed: int,
) -> list[list[dict]]:
    """覆盖优先地构建多轮抽查批次。"""
    if not samples or sample_count <= 0 or rounds <= 0:
        return []

    rng = random.Random(seed)
    pool = list(samples)
    rng.shuffle(pool)

    effective_count = min(sample_count, len(pool))
    cursor = 0
    batches: list[list[dict]] = []

    for _ in range(rounds):
        if effective_count == len(pool):
            batch = list(pool)
            rng.shuffle(batch)
            batches.append(batch)
            continue

        if cursor + effective_count <= len(pool):
            batch = pool[cursor:cursor + effective_count]
            cursor += effective_count
        else:
            batch = pool[cursor:]
            rng.shuffle(pool)
            needed = effective_count - len(batch)
            batch.extend(pool[:needed])
            cursor = needed

        batches.append(batch)

    return batches


def summarize_round_result(round_index: int, result: dict) -> dict:
    """提取每轮审计的关键信息。"""
    summary = result.get("audit_summary", {}) if isinstance(result, dict) else {}
    return {
        "round_index": round_index,
        "overall_risk": summary.get("overall_risk", "unknown"),
        "final_verdict": summary.get("final_verdict", "unknown"),
        "total_samples": summary.get("total_samples", 0),
        "fail_count": summary.get("fail_count", 0),
        "borderline_count": summary.get("borderline_count", 0),
    }


def run_quality_audit(
    game: str = "mmorpg",
    samples: list[dict] | None = None,
    model: str | None = None,
    sample_count: int = DEFAULT_AUDIT_SAMPLE_COUNT,
    rounds: int = DEFAULT_AUDIT_ROUNDS,
    seed: int = DEFAULT_AUDIT_SEED,
    input_path: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> dict:
    """
    执行多轮训练数据质量抽查。

    Returns:
        审计摘要信息。
    """
    print(f"\n{'='*60}")
    print(f"  Step 7: 训练数据质量抽查 ({game})")
    print(f"{'='*60}")

    if sample_count <= 0 or rounds <= 0:
        print("  [SKIP] Step 7: 抽查样本数或抽查轮数为 0，跳过质量抽查")
        return {
            "status": "skipped",
            "sample_count": sample_count,
            "rounds": rounds,
        }

    if input_path is None:
        input_path = os.path.join(PROJECT_DIR, "output", game, "merged_all.jsonl")

    if samples is None:
        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"合并文件不存在: {input_path}")
        samples = load_jsonl(input_path)

    if not samples:
        print("  [SKIP] Step 7: 没有可抽查的样本，跳过")
        return {
            "status": "empty",
            "sample_count": sample_count,
            "rounds": rounds,
        }

    commands_path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    if not os.path.isfile(commands_path):
        raise FileNotFoundError(f"Commands 文件不存在: {commands_path}")

    commands = load_commands(commands_path)
    command_registry = compact_file(commands_path)
    game_background = discover_game_background(game)
    optional_stats = build_optional_stats(samples, commands)
    template = load_prompt_template()
    batches = create_round_batches(samples, sample_count, rounds, seed)

    output_dir = os.path.join(PROJECT_DIR, "output", game, "quality_audit")
    os.makedirs(output_dir, exist_ok=True)

    requested_sample_count = sample_count
    actual_sample_count = min(sample_count, len(samples))

    print(f"  数据集样本数: {len(samples)}")
    print(f"  抽查计划: {rounds} 轮 × 每轮 {actual_sample_count} 条（请求值 {requested_sample_count}）")
    print(f"  抽样种子: {seed}")

    round_summaries: list[dict] = []
    round_files: list[str] = []
    errors: list[dict] = []

    for round_index, batch in enumerate(batches, start=1):
        print(f"\n  第 {round_index} 轮: 抽查 {len(batch)} 条样本")
        batch_context = build_batch_context(
            game=game,
            input_path=input_path,
            round_index=round_index,
            rounds=rounds,
            requested_sample_count=requested_sample_count,
            actual_sample_count=len(batch),
            batch=batch,
            seed=seed,
        )
        prompt = build_prompt(
            template=template,
            game_background=game_background,
            command_registry=command_registry,
            sample_batch=batch,
            batch_context=batch_context,
            optional_stats=optional_stats,
        )

        try:
            audit_result = call_llm_json(
                prompt,
                model=model,
                temperature=0.2,
                max_tokens=12000,
                think_mode=think_mode,
                think_level=think_level,
            )
            if not isinstance(audit_result, dict):
                raise ValueError("质量抽查返回不是 JSON 对象")

            round_payload = {
                "round_index": round_index,
                "game": game,
                "input_path": input_path,
                "sample_count_requested": requested_sample_count,
                "sample_count_actual": len(batch),
                "sampling_seed": seed,
                "batch_context": batch_context,
                "optional_stats": optional_stats,
                "sample_batch": batch,
                "audit_result": audit_result,
            }

            round_path = os.path.join(output_dir, f"audit_round_{round_index:02d}.json")
            with open(round_path, "w", encoding="utf-8") as f:
                json.dump(round_payload, f, ensure_ascii=False, indent=2)

            round_files.append(round_path)
            summary = summarize_round_result(round_index, audit_result)
            round_summaries.append(summary)
            print(
                f"    [OK] 风险={summary['overall_risk']}，结论={summary['final_verdict']}，"
                f"fail={summary['fail_count']}，borderline={summary['borderline_count']}"
            )
        except Exception as e:
            error = {"round_index": round_index, "error": str(e)}
            errors.append(error)
            print(f"    [ERROR] 第 {round_index} 轮失败: {e}")

    summary_payload = {
        "status": "completed_with_errors" if errors else "completed",
        "game": game,
        "input_path": input_path,
        "sample_count_requested": requested_sample_count,
        "sample_count_actual": actual_sample_count,
        "rounds_requested": rounds,
        "rounds_completed": len(round_summaries),
        "sampling_seed": seed,
        "output_dir": output_dir,
        "round_summaries": round_summaries,
        "round_files": round_files,
        "errors": errors,
    }

    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_payload, f, ensure_ascii=False, indent=2)

    print(f"\n  [OK] 质量抽查摘要已保存到: {summary_path}")
    if errors:
        print(f"  [WARN] 有 {len(errors)} 轮抽查失败，请查看 summary.json")

    return summary_payload


def main():
    args = parse_args()
    run_quality_audit(
        game=args.game,
        model=args.model,
        sample_count=args.sample_count,
        rounds=args.rounds,
        seed=args.seed,
        input_path=args.input_path,
        think_mode=args.think_mode,
        think_level=args.think_level,
    )


if __name__ == "__main__":
    main()
