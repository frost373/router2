#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练数据生成器 — 主入口
串联整个流程：词库生成 → 别名扩写 → 模板样本 → 对抗样本 → paraphrase → 合并
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
        "--command_id", default=None, help="只处理指定的 command_id（用于测试）"
    )
    parser.add_argument(
        "--model", default=None, help="LLM 模型名称（默认 glm-5）"
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
    return parser.parse_args()


def merge_outputs(game: str, command_id: str | None = None):
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

    # 确定要合并哪些 command 目录
    if command_id:
        cmd_dirs = [command_id]
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

    # 全量合并到 game 目录
    merged_all_path = os.path.join(game_dir, "merged_all.jsonl")
    with open(merged_all_path, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 统计
    label_counts: dict[str, int] = {}
    for s in all_samples:
        label = s.get("label", "unknown")
        label_counts[label] = label_counts.get(label, 0) + 1

    type_counts: dict[str, int] = {}
    for s in all_samples:
        st = s.get("source_type", "unknown")
        type_counts[st] = type_counts.get(st, 0) + 1

    print(f"\n  合并完成: {len(all_samples)} 条")
    print(f"  保存到: {merged_all_path}")

    print(f"\n  📊 按 label 分布:")
    total = len(all_samples)
    for label, count in sorted(label_counts.items()):
        pct = count / total * 100 if total else 0
        print(f"     {label}: {count} ({pct:.1f}%)")

    print(f"\n  📊 按 source_type 分布:")
    for st, count in sorted(type_counts.items()):
        pct = count / total * 100 if total else 0
        print(f"     {st}: {count} ({pct:.1f}%)")

    return all_samples


def main():
    args = parse_args()

    print("=" * 60)
    print("  训练数据生成器 — 模板主导 + 扰动扩写")
    print("=" * 60)
    print(f"  游戏类型: {args.game}")
    print(f"  目标 command: {args.command_id or '全部'}")
    print(f"  模型: {args.model or '默认 (glm-5)'}")

    # Step 1: 词库生成
    if not args.skip_vocab:
        from generate_vocab import generate_vocab
        generate_vocab(args.game, command_id=args.command_id, model=args.model)
    else:
        print("\n  ⏭️  跳过词库生成（使用已有词库）")

    # Step 2: 别名扩写
    if not args.skip_aliases:
        from expand_aliases import expand_aliases
        expand_aliases(args.game, command_id=args.command_id, model=args.model)
    else:
        print("\n  ⏭️  跳过别名扩写（使用已有扩写结果）")

    # Step 3: 模板样本
    from generate_template_samples import generate_template_samples
    generate_template_samples(
        args.game,
        command_id=args.command_id,
        samples_per_command=args.template_count,
    )

    # Step 4: 对抗样本
    from generate_adversarial_samples import generate_adversarial_samples
    generate_adversarial_samples(
        args.game,
        command_id=args.command_id,
        max_source_per_command=args.adversarial_source,
        model=args.model,
    )

    # Step 5: paraphrase
    from generate_paraphrase_samples import generate_paraphrase_samples
    generate_paraphrase_samples(
        args.game,
        command_id=args.command_id,
        max_source_per_command=args.paraphrase_source,
        model=args.model,
    )

    # Step 6: 合并
    merge_outputs(args.game, command_id=args.command_id)

    print(f"\n{'='*60}")
    print(f"  ✅ 全部完成！")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
