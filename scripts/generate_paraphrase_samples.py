#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自由 paraphrase 样本生成器
使用 LLM 对少量正样本做自由改写，增加自然度
"""

import json
import os
import random
import sys

from llm_client import call_llm_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

# 每批发给 LLM 的样本数量
BATCH_SIZE = 10


def load_prompt_template() -> str:
    """加载 paraphrase 提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "paraphrase_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def paraphrase_batch(
    samples: list[dict],
    template: str,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> list[dict]:
    """
    对一批样本做 paraphrase

    Returns:
        改写后的样本列表
    """
    input_items = [
        {"text": s["text"], "command_id": s["command_id"], "label": s["label"], "slots": s.get("slots", {})}
        for s in samples
    ]
    input_json = json.dumps(input_items, ensure_ascii=False, indent=2)
    prompt = template.replace("{{samples}}", input_json)

    result = call_llm_json(
        prompt,
        model=model,
        temperature=0.9,
        max_tokens=8192,
        think_mode=think_mode,
        think_level=think_level,
    )

    paraphrased = []
    for item in result:
        command_id = item.get("command_id", "")
        label = item.get("label", "quick_command")
        source = item.get("source", "")

        # 从原样本中查找对应的 slots
        orig_slots = {}
        for s in samples:
            if s["text"] == source and s["command_id"] == command_id:
                orig_slots = s.get("slots", {})
                break

        for para_text in item.get("paraphrases", []):
            if not para_text:
                continue

            # 校验: slots 的值必须被改写后的 text 包含
            valid = True
            for slot_val in orig_slots.values():
                if slot_val not in para_text:
                    valid = False
                    break

            if valid:
                paraphrased.append({
                    "text": para_text,
                    "label": label,
                    "command_id": command_id,
                    "slots": orig_slots,
                    "source_text": source,
                })
            else:
                print(f"    [WARN] 过滤无效改写(缺失槽位值): {para_text} (slots: {orig_slots})")

    return paraphrased


def generate_paraphrase_samples(
    game: str = "mmorpg",
    command_id: str | None = None,
    max_source_per_command: int = 5,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> list[dict]:
    """
    生成自由 paraphrase 样本

    Args:
        game: 游戏类型
        command_id: 如果指定，只处理该 command
        max_source_per_command: 每个 command 选多少条正样本做改写
        model: LLM 模型

    Returns:
        改写样本列表
    """
    print(f"\n{'='*60}")
    print(f"  Step 5: 自由 Paraphrase 样本生成 ({game})")
    print(f"{'='*60}")

    # 加载正样本
    positive = []
    game_dir = os.path.join(PROJECT_DIR, "output", game)
    if command_id:
        path = os.path.join(game_dir, command_id, "template.jsonl")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"模板样本文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    positive.append(json.loads(line))
    else:
        for cid_dir in os.listdir(game_dir):
            path = os.path.join(game_dir, cid_dir, "template.jsonl")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            positive.append(json.loads(line))

    # 按 command_id 分组，每组选 max_source_per_command 条
    by_cmd: dict[str, list[dict]] = {}
    for s in positive:
        cid = s["command_id"]
        by_cmd.setdefault(cid, []).append(s)

    selected = []
    for cid, samples in by_cmd.items():
        chosen = random.sample(samples, min(len(samples), max_source_per_command))
        selected.extend(chosen)

    print(f"  从 {len(positive)} 条正样本中选取 {len(selected)} 条做改写")

    # 分批调用 LLM
    template = load_prompt_template()
    all_paraphrased = []

    for i in range(0, len(selected), BATCH_SIZE):
        batch = selected[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(selected) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n  批次 {batch_num}/{total_batches} ({len(batch)} 条)...")

        try:
            results = paraphrase_batch(
                batch,
                template,
                model=model,
                think_mode=think_mode,
                think_level=think_level,
            )
            all_paraphrased.extend(results)
            print(f"    生成 {len(results)} 条改写样本")
        except Exception as e:
            print(f"    [ERROR] 批次 {batch_num} 失败: {e}")

    print(f"\n  [OK] 总计 {len(all_paraphrased)} 条 paraphrase 样本")

    # 保存（按 command_id 分目录）
    by_cmd: dict[str, list[dict]] = {}
    for s in all_paraphrased:
        cid = s.get("command_id", "_shared")
        by_cmd.setdefault(cid, []).append(s)

    for cid, samples_list in by_cmd.items():
        cmd_dir = os.path.join(PROJECT_DIR, "output", game, cid)
        os.makedirs(cmd_dir, exist_ok=True)
        out_path = os.path.join(cmd_dir, "paraphrase.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for s in samples_list:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return all_paraphrased


if __name__ == "__main__":
    game = sys.argv[1] if len(sys.argv) > 1 else "mmorpg"
    cid = sys.argv[2] if len(sys.argv) > 2 else None
    generate_paraphrase_samples(game, command_id=cid)
