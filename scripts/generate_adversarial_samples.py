#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小编辑对抗样本生成器
从正样本出发，使用 LLM 做"最小编辑"生成 hard negative
"""

import json
import os
import sys

from llm_client import call_llm_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

# 每批发给 LLM 的正样本数量
BATCH_SIZE = 10


def load_prompt_template() -> str:
    """加载对抗样本提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "adversarial_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_adversarial_batch(
    positive_samples: list[dict],
    template: str,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> list[dict]:
    """
    对一批正样本生成对抗样本

    Returns:
        对抗样本列表
    """
    # 建立 source text -> command_id 映射
    source_cmd_map = {s["text"]: s["command_id"] for s in positive_samples}

    # 只传 text 和 command_id 给 LLM
    input_items = [
        {"text": s["text"], "command_id": s["command_id"]}
        for s in positive_samples
    ]
    input_json = json.dumps(input_items, ensure_ascii=False, indent=2)
    prompt = template.replace("{{positive_samples}}", input_json)

    result = call_llm_json(
        prompt,
        model=model,
        temperature=0.8,
        max_tokens=8192,
        think_mode=think_mode,
        think_level=think_level,
    )

    adversarial = []
    for item in result:
        source = item.get("source", "")
        src_cid = source_cmd_map.get(source, "")
        for variant in item.get("variants", []):
            text = variant.get("text", "")
            label = variant.get("label", "tactical")
            edit_type = variant.get("edit_type", "unknown")

            if text and label in ("tactical", "chat"):
                adversarial.append({
                    "text": text,
                    "label": label,
                    "command_id": "",  # 对抗样本不属于任何 command
                    "slots": {},
                    "source_text": source,
                    "source_command_id": src_cid,
                    "edit_type": edit_type,
                })

    return adversarial


def generate_adversarial_samples(
    game: str = "mmorpg",
    command_id: str | None = None,
    max_source_per_command: int = 10,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> list[dict]:
    """
    生成最小编辑对抗样本

    Args:
        game: 游戏类型
        command_id: 如果指定，只处理该 command 的正样本
        max_source_per_command: 每个 command 选多少条正样本作为源
        model: LLM 模型

    Returns:
        对抗样本列表
    """
    print(f"\n{'='*60}")
    print(f"  Step 4: 最小编辑对抗样本生成 ({game})")
    print(f"{'='*60}")

    # 加载正样本
    positive = []
    game_dir = os.path.join(PROJECT_DIR, "output", game)
    if command_id:
        # 加载单个 command
        path = os.path.join(game_dir, command_id, "template.jsonl")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"模板样本文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    positive.append(json.loads(line))
    else:
        # 加载所有 command
        for cid_dir in os.listdir(game_dir):
            path = os.path.join(game_dir, cid_dir, "template.jsonl")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            positive.append(json.loads(line))

    # 按 command_id 分组，每组选 max_source_per_command 条
    import random
    by_cmd: dict[str, list[dict]] = {}
    for s in positive:
        cid = s["command_id"]
        by_cmd.setdefault(cid, []).append(s)

    selected = []
    for cid, samples in by_cmd.items():
        chosen = random.sample(samples, min(len(samples), max_source_per_command))
        selected.extend(chosen)

    print(f"  从 {len(positive)} 条正样本中选取 {len(selected)} 条作为源")

    # 分批调用 LLM
    template = load_prompt_template()
    all_adversarial = []

    for i in range(0, len(selected), BATCH_SIZE):
        batch = selected[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(selected) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"\n  批次 {batch_num}/{total_batches} ({len(batch)} 条)...")

        try:
            results = generate_adversarial_batch(
                batch,
                template,
                model=model,
                think_mode=think_mode,
                think_level=think_level,
            )
            all_adversarial.extend(results)
            print(f"    生成 {len(results)} 条对抗样本")
        except Exception as e:
            print(f"    [ERROR] 批次 {batch_num} 失败: {e}")

    # 统计
    tactical_count = sum(1 for s in all_adversarial if s["label"] == "tactical")
    chat_count = sum(1 for s in all_adversarial if s["label"] == "chat")

    print(f"\n  [OK] 总计 {len(all_adversarial)} 条对抗样本")
    print(f"     tactical: {tactical_count}")
    print(f"     chat: {chat_count}")

    # 保存（按 source command 分目录）
    by_source_cmd: dict[str, list[dict]] = {}
    for s in all_adversarial:
        src_cmd = s.get("source_command_id", "_shared")
        by_source_cmd.setdefault(src_cmd, []).append(s)

    for src_cid, samples_list in by_source_cmd.items():
        cmd_dir = os.path.join(PROJECT_DIR, "output", game, src_cid)
        os.makedirs(cmd_dir, exist_ok=True)
        out_path = os.path.join(cmd_dir, "adversarial.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for s in samples_list:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return all_adversarial


if __name__ == "__main__":
    game = sys.argv[1] if len(sys.argv) > 1 else "mmorpg"
    cid = sys.argv[2] if len(sys.argv) > 2 else None
    generate_adversarial_samples(game, command_id=cid)
