#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局负样本生成器
基于 command_registry 压缩格式，使用 LLM 批量生成全局级 tactical/chat 负样本。
使用 Embedding 模型对结果做语义去重。
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

# 默认 sample_plan（每轮生成的各 bucket 数量）
DEFAULT_SAMPLE_PLAN = {
    "tactical_self_action": 6,
    "tactical_team_plan": 6,
    "tactical_conditional": 6,
    "tactical_multi_step": 6,
    "tactical_missing_slot": 6,
    "tactical_out_of_registry_teammate_action": 6,
    "tactical_ambiguous": 6,
    "chat_emotion": 6,
    "chat_noise": 4,
    "chat_out_of_game": 4,
}

# 默认游戏背景（MMORPG）
DEFAULT_GAME_BACKGROUND = (
    "这是一款经典的东方玄幻/魔幻MMORPG，包含战士、法师、牧师、射手等职业，"
    "有副本、野外、PVP等场景。玩家可以组队挑战地下城、世界boss、竞技场等。"
    "AI队友是一个可以接受玩家语音/文字命令的智能NPC队友。"
)


def load_prompt_template() -> str:
    """加载全局负样本提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "global_negative_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_one_round(
    compact_commands: str,
    sample_plan: dict,
    game_background: str,
    template: str,
    model: str | None = None,
    round_num: int = 1,
) -> list[dict]:
    """
    执行一轮全局负样本生成。

    Returns:
        本轮生成的样本列表
    """
    import re
    from llm_client import call_llm

    prompt = template.replace("{{command_registry_json}}", compact_commands)
    prompt = prompt.replace(
        "{{sample_plan_json}}",
        json.dumps(sample_plan, ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace("{{game_background}}", game_background)

    print(f"\n  第 {round_num} 轮: 调用 LLM 生成中...")
    raw = call_llm(prompt, model=model, temperature=0.9, max_tokens=8192)

    # 清洗 LLM 输出
    text = raw.strip()

    # 去掉 <think> 块
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 尝试提取 ```json ... ``` 中的内容
    match = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    # 去除 JSON 字符串中的控制字符（保留换行符以保持结构）
    text = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # 尝试找到 JSON 数组边界
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    [WARN] JSON 解析失败: {e}")
        print(f"    原始文本前 300 字符: {text[:300]}")
        return []

    # 校验格式
    valid = []
    for item in result:
        inp = item.get("input", "")
        label = item.get("label", {})
        meta = item.get("meta", {})
        label_type = label.get("type", "") if isinstance(label, dict) else ""
        bucket = meta.get("bucket", "") if isinstance(meta, dict) else ""

        if inp and label_type in ("tactical", "chat") and bucket:
            valid.append(item)
        else:
            print(f"    [WARN] 跳过无效样本: {item}")

    return valid


def generate_global_negatives(
    game: str = "mmorpg",
    model: str | None = None,
    rounds: int = 3,
    dedup_threshold: float = 0.92,
    sample_plan: dict | None = None,
    game_background: str | None = None,
) -> list[dict]:
    """
    生成全局负样本。

    Args:
        game: 游戏类型
        model: LLM 模型名称
        rounds: 生成轮数
        dedup_threshold: 去重余弦相似度阈值
        sample_plan: 各 bucket 生成数量配置
        game_background: 游戏背景描述

    Returns:
        去重后的全局负样本列表
    """
    print(f"\n{'='*60}")
    print(f"  全局负样本生成 ({game})")
    print(f"{'='*60}")

    # 使用 Commands 压缩器获取 compact 格式
    sys.path.insert(0, SCRIPT_DIR)
    from command_registry_compact import compact_file

    commands_path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    if not os.path.isfile(commands_path):
        raise FileNotFoundError(f"Commands 文件不存在: {commands_path}")

    compact_commands = compact_file(commands_path)
    print(f"  Commands 压缩完成 ({len(compact_commands)} 字符)")

    # 加载参数
    plan = sample_plan or DEFAULT_SAMPLE_PLAN
    bg = game_background or DEFAULT_GAME_BACKGROUND
    template = load_prompt_template()

    total_per_round = sum(plan.values())
    print(f"  计划: {rounds} 轮 × 每轮 ~{total_per_round} 条 = ~{rounds * total_per_round} 条")
    print(f"  去重阈值: {dedup_threshold}")

    # 多轮生成
    all_samples: list[dict] = []
    for r in range(1, rounds + 1):
        try:
            batch = generate_one_round(
                compact_commands, plan, bg, template,
                model=model, round_num=r,
            )
            all_samples.extend(batch)
            print(f"    [OK] 第 {r} 轮生成 {len(batch)} 条")
        except Exception as e:
            print(f"    [ERROR] 第 {r} 轮失败: {e}")

    print(f"\n  合计生成: {len(all_samples)} 条（去重前）")

    if not all_samples:
        print("  [WARN] 未生成任何样本")
        return []

    # Embedding 语义去重
    print(f"\n  开始 Embedding 语义去重...")
    from embedding_client import deduplicate_by_embedding

    deduped, stats = deduplicate_by_embedding(
        all_samples, text_key="input", threshold=dedup_threshold,
    )
    print(f"  去重完成: {stats['before']} → {stats['after']}（移除 {stats['removed']} 条）")

    # 统计 bucket 分布
    bucket_counts: dict[str, int] = {}
    for s in deduped:
        bucket = s.get("meta", {}).get("bucket", "unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    label_counts: dict[str, int] = {}
    for s in deduped:
        lt = s.get("label", {}).get("type", "unknown")
        label_counts[lt] = label_counts.get(lt, 0) + 1

    print(f"\n  [STATS] 按 label 分布:")
    for label, count in sorted(label_counts.items()):
        pct = count / len(deduped) * 100 if deduped else 0
        print(f"     {label}: {count} ({pct:.1f}%)")

    print(f"\n  [STATS] 按 bucket 分布:")
    for bucket, count in sorted(bucket_counts.items()):
        print(f"     {bucket}: {count}")

    # 保存输出
    out_dir = os.path.join(PROJECT_DIR, "output", game)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "global_negatives.jsonl")

    with open(out_path, "w", encoding="utf-8") as f:
        for s in deduped:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"\n  [OK] 已保存到 {out_path}")
    print(f"     共 {len(deduped)} 条全局负样本")

    return deduped


# ── CLI 入口 ──────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全局负样本生成器")
    parser.add_argument("--game", default="mmorpg", help="游戏类型（默认 mmorpg）")
    parser.add_argument("--model", default=None, help="LLM 模型名称")
    parser.add_argument("--rounds", type=int, default=3, help="生成轮数（默认 3）")
    parser.add_argument(
        "--dedup_threshold", type=float, default=0.92,
        help="去重余弦相似度阈值（默认 0.92）",
    )
    args = parser.parse_args()

    generate_global_negatives(
        game=args.game,
        model=args.model,
        rounds=args.rounds,
        dedup_threshold=args.dedup_threshold,
    )
