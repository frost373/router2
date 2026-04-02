#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练数据生成器 — 主入口
串联整个流程：词库生成 → 别名扩写 → 模板样本 → 对抗样本 → paraphrase → 合并 → 质量抽查
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)


def parse_args():
    parser = argparse.ArgumentParser(
        description="训练数据生成器 — 模板主导 + 扰动扩写"
    )
    parser.add_argument(
        "--game", default="mmorpg", help="游戏类型（对应 commands/ 下的 JSON 文件名）"
    )
    parser.add_argument(
        "--command_id",
        dest="command_ids",
        action="append",
        default=[],
        help="可重复指定 command_id；不传表示处理全部 command"
    )
    parser.add_argument(
        "--model", default=None, help="LLM 模型名称（默认 glm-5）"
    )
    parser.add_argument(
        "--think_mode", action="store_true", help="开启思考模式"
    )
    parser.add_argument(
        "--think_level", default="high", help="思考等级（low/medium/high）"
    )
    parser.add_argument(
        "--skip_vocab", action="store_true", help="跳过词库生成（使用已有词库）"
    )
    parser.add_argument(
        "--skip_aliases", action="store_true", help="跳过别名扩写（使用已有扩写结果）"
    )
    parser.add_argument(
        "--template_count", type=int, default=40,
        help="每个 command 生成的模板样本数（默认 40）"
    )
    parser.add_argument(
        "--adversarial_source", type=int, default=10,
        help="每个 command 选多少条正样本生成对抗样本（默认 10）"
    )
    parser.add_argument(
        "--paraphrase_source", type=int, default=5,
        help="每个 command 选多少条正样本做 paraphrase（默认 5）"
    )
    parser.add_argument(
        "--skip_global_negatives", action="store_true",
        help="跳过全局负样本生成（使用已有结果）"
    )
    parser.add_argument(
        "--global_neg_rounds", type=int, default=3,
        help="全局负样本生成轮数（默认 3）"
    )
    parser.add_argument(
        "--dedup_threshold", type=float, default=0.92,
        help="全局负样本去重余弦相似度阈值（默认 0.92）"
    )
    parser.add_argument(
        "--audit_sample_count", type=int, default=12,
        help="单次质量抽查样本数（默认 12，设为 0 可跳过）"
    )
    parser.add_argument(
        "--audit_rounds", type=int, default=2,
        help="质量抽查轮数（默认 2，设为 0 可跳过）"
    )
    return parser.parse_args()


def load_registry_command_ids(game: str) -> list[str]:
    path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    commands = data.get("commands", data)
    return [cmd["command_id"] for cmd in commands]


def normalize_command_selection(game: str, command_ids: list[str]) -> list[str] | None:
    selected: list[str] = []
    seen: set[str] = set()

    for command_id in command_ids:
        cid = command_id.strip()
        if not cid or cid in seen:
            continue
        selected.append(cid)
        seen.add(cid)

    if not selected:
        return None

    available = set(load_registry_command_ids(game))
    missing = [cid for cid in selected if cid not in available]
    if missing:
        raise ValueError(f"未找到 command_id: {', '.join(missing)}")

    return selected


def iter_selected_commands(command_ids: list[str] | None) -> list[str | None]:
    return command_ids if command_ids else [None]


def format_command_scope(command_ids: list[str] | None) -> str:
    if not command_ids:
        return "全部"
    return ", ".join(command_ids)


def merge_outputs(game: str, command_ids: list[str] | None = None):
    """合并所有输出到最终 JSONL"""
    print(f"\n{'='*60}")
    print(f"  Step 6: 合并输出")
    print(f"{'='*60}")

    game_dir = os.path.join(PROJECT_DIR, "output", game)
    file_types = [
        ("template.jsonl", "template"),
        ("adversarial.jsonl", "adversarial"),
        ("paraphrase.jsonl", "paraphrase"),
    ]

    # 全局负样本（不在 command 子目录中，单独处理）
    global_neg_path = os.path.join(game_dir, "global_negatives.jsonl")
    global_neg_samples = []
    if os.path.isfile(global_neg_path):
        with open(global_neg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    sample = json.loads(line)
                    sample.setdefault("source_type", "global_negative")
                    global_neg_samples.append(sample)
        print(f"  全局负样本: {len(global_neg_samples)} 条")

    # 确定要合并哪些 command 目录
    if command_ids:
        cmd_dirs = command_ids
    else:
        cmd_dirs = [
            d for d in os.listdir(game_dir)
            if os.path.isdir(os.path.join(game_dir, d))
        ]

    all_samples = []
    for cid in sorted(cmd_dirs):
        cmd_dir = os.path.join(game_dir, cid)
        cmd_samples = []

        for filename, source_type in file_types:
            filepath = os.path.join(cmd_dir, filename)
            if not os.path.isfile(filepath):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sample = json.loads(line)
                        sample["source_type"] = source_type
                        cmd_samples.append(sample)

        if cmd_samples:
            # 每个 command 目录写 merged.jsonl
            merged_path = os.path.join(cmd_dir, "merged.jsonl")
            with open(merged_path, "w", encoding="utf-8") as f:
                for s in cmd_samples:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
            print(f"  {cid}: {len(cmd_samples)} 条 → {merged_path}")
            all_samples.extend(cmd_samples)

    # 加入全局负样本
    all_samples.extend(global_neg_samples)

    # 全量合并到 game 目录
    merged_all_path = os.path.join(game_dir, "merged_all.jsonl")
    with open(merged_all_path, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 统计
    label_counts: dict[str, int] = {}
    for s in all_samples:
        label = s.get("label", "unknown")
        # 兼容 label 为 dict（如 {"type": "tactical"}）和字符串两种格式
        if isinstance(label, dict):
            label = label.get("type", "unknown")
        label_counts[label] = label_counts.get(label, 0) + 1

    type_counts: dict[str, int] = {}
    for s in all_samples:
        st = s.get("source_type", "unknown")
        type_counts[st] = type_counts.get(st, 0) + 1

    print(f"\n  合并完成: {len(all_samples)} 条")
    print(f"  保存到: {merged_all_path}")

    print(f"\n  [STATS] 按 label 分布:")
    total = len(all_samples)
    for label, count in sorted(label_counts.items()):
        pct = count / total * 100 if total else 0
        print(f"     {label}: {count} ({pct:.1f}%)")

    print(f"\n  [STATS] 按 source_type 分布:")
    for st, count in sorted(type_counts.items()):
        pct = count / total * 100 if total else 0
        print(f"     {st}: {count} ({pct:.1f}%)")

    return all_samples


def clone_with_source_type(samples: list[dict], source_type: str) -> list[dict]:
    """Return shallow copies tagged with the source type for current-run audit."""
    return [{**sample, "source_type": source_type} for sample in samples]


def main():
    args = parse_args()
    selected_command_ids = normalize_command_selection(args.game, args.command_ids)

    print("=" * 60)
    print("  训练数据生成器 — 模板主导 + 扰动扩写")
    print("=" * 60)
    print(f"  游戏类型: {args.game}")
    print(f"  目标 command: {format_command_scope(selected_command_ids)}")
    print(f"  模型: {args.model or '默认 (glm-5)'}")
    print(f"  思考模式: {'开启' if args.think_mode else '关闭'}")

    # Step 1: 词库生成
    if not args.skip_vocab:
        from generate_vocab import generate_vocab
        for command_id in iter_selected_commands(selected_command_ids):
            generate_vocab(
                args.game,
                command_id=command_id,
                model=args.model,
                think_mode=args.think_mode,
                think_level=args.think_level,
            )
    else:
        print("\n  [SKIP] Step 1: 跳过词库生成（使用已有词库）")

    # Step 2: 别名扩写
    if not args.skip_aliases:
        from expand_aliases import expand_aliases
        for command_id in iter_selected_commands(selected_command_ids):
            expand_aliases(
                args.game,
                command_id=command_id,
                model=args.model,
                think_mode=args.think_mode,
                think_level=args.think_level,
            )
    else:
        print("\n  [SKIP] Step 2: 跳过别名扩写（使用已有扩写结果）")

    # Step 3: 模板样本
    from generate_template_samples import generate_template_samples
    template_samples = []
    for command_id in iter_selected_commands(selected_command_ids):
        template_samples.extend(generate_template_samples(
            args.game,
            command_id=command_id,
            samples_per_command=args.template_count,
        ))

    # Step 4: 对抗样本
    from generate_adversarial_samples import generate_adversarial_samples
    adversarial_samples = []
    for command_id in iter_selected_commands(selected_command_ids):
        adversarial_samples.extend(generate_adversarial_samples(
            args.game,
            command_id=command_id,
            max_source_per_command=args.adversarial_source,
            model=args.model,
            think_mode=args.think_mode,
            think_level=args.think_level,
        ))

    # Step 5: paraphrase
    from generate_paraphrase_samples import generate_paraphrase_samples
    paraphrase_samples = []
    for command_id in iter_selected_commands(selected_command_ids):
        paraphrase_samples.extend(generate_paraphrase_samples(
            args.game,
            command_id=command_id,
            max_source_per_command=args.paraphrase_source,
            model=args.model,
            think_mode=args.think_mode,
            think_level=args.think_level,
        ))

    # Step 5.5: 全局负样本
    if not args.skip_global_negatives:
        from generate_global_negatives import generate_global_negatives
        generate_global_negatives(
            game=args.game,
            model=args.model,
            rounds=args.global_neg_rounds,
            dedup_threshold=args.dedup_threshold,
            think_mode=args.think_mode,
            think_level=args.think_level,
        )
    else:
        print("\n  [SKIP] Step 5.5: 跳过全局负样本生成（使用已有结果）")

    # Step 6: 合并
    merge_outputs(args.game, command_ids=selected_command_ids)

    current_run_samples = []
    current_run_samples.extend(clone_with_source_type(template_samples, "template"))
    current_run_samples.extend(clone_with_source_type(adversarial_samples, "adversarial"))
    current_run_samples.extend(clone_with_source_type(paraphrase_samples, "paraphrase"))

    # Step 7: 质量抽查
    try:
        from audit_training_data import run_quality_audit
        run_quality_audit(
            game=args.game,
            samples=current_run_samples,
            model=args.model,
            sample_count=args.audit_sample_count,
            rounds=args.audit_rounds,
            input_path=(
                f"current_run/{args.game}/{selected_command_ids[0]}"
                if selected_command_ids and len(selected_command_ids) == 1
                else (
                    f"current_run/{args.game}/selected_commands"
                    if selected_command_ids
                    else f"current_run/{args.game}/all_commands"
                )
            ),
            think_mode=args.think_mode,
            think_level=args.think_level,
        )
    except Exception as e:
        print(f"\n  [WARN] 质量抽查失败: {e}")

    print(f"\n{'='*60}")
    print(f"  [OK] 全部完成！")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
