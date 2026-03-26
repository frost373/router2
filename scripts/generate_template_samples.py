#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序模板样本生成器
用扩写后的别名模板 × 词库进行填槽，生成正样本 (label=quick_command)
"""

import json
import os
import random
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)


def load_commands(game: str) -> list[dict]:
    """加载 commands"""
    path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["commands"]


def load_vocab(game: str, command_id: str) -> dict:
    """加载特定 command 的词库"""
    path = os.path.join(PROJECT_DIR, "output", game, command_id, "vocab.json")
    if not os.path.isfile(path):
        return {"targets": [], "uses": [], "target_use_pairs": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_expanded_aliases(game: str, command_id: str | None = None) -> dict:
    """加载扩写后的别名"""
    game_dir = os.path.join(PROJECT_DIR, "output", game)
    result = {}
    if command_id:
        path = os.path.join(game_dir, command_id, "aliases.json")
        with open(path, "r", encoding="utf-8") as f:
            result.update(json.load(f))
    else:
        for cid_dir in os.listdir(game_dir):
            alias_path = os.path.join(game_dir, cid_dir, "aliases.json")
            if os.path.isfile(alias_path):
                with open(alias_path, "r", encoding="utf-8") as f:
                    result.update(json.load(f))
    return result


def get_slot_names(cmd: dict) -> list[str]:
    """获取 command 的 slot name 列表"""
    return [s["name"] for s in cmd.get("slots", [])]


def fill_template(
    template: str, slot_names: list[str], vocab: dict
) -> tuple[str, dict]:
    """
    填充模板中的槽位

    Returns:
        (填充后的文本, 槽位值字典)
    """
    slots_filled = {}
    text = template

    if "target" in slot_names and "use" in slot_names:
        # 双参数：优先使用配对
        pairs = vocab.get("target_use_pairs", [])
        if pairs:
            pair = random.choice(pairs)
            target_val = pair["target"]
            use_val = pair["use"]
        else:
            target_val = random.choice(vocab["targets"])
            use_val = random.choice(vocab["uses"])
        text = text.replace("{target}", target_val)
        text = text.replace("{use}", use_val)
        slots_filled["target"] = target_val
        slots_filled["use"] = use_val

    elif "target" in slot_names:
        target_val = random.choice(vocab["targets"])
        text = text.replace("{target}", target_val)
        slots_filled["target"] = target_val

    elif "use" in slot_names:
        use_val = random.choice(vocab["uses"])
        text = text.replace("{use}", use_val)
        slots_filled["use"] = use_val

    return text, slots_filled


def generate_template_samples(
    game: str = "mmorpg",
    command_id: str | None = None,
    samples_per_command: int = 40,
) -> list[dict]:
    """
    生成程序模板样本

    Args:
        game: 游戏类型
        command_id: 如果指定，只处理该 command
        samples_per_command: 每个 command 生成多少条

    Returns:
        样本列表
    """
    print(f"\n{'='*60}")
    print(f"  Step 3: 程序模板样本生成 ({game})")
    print(f"{'='*60}")

    commands = load_commands(game)
    aliases_data = load_expanded_aliases(game, command_id)

    if command_id:
        commands = [c for c in commands if c["command_id"] == command_id]

    all_samples = []

    for cmd in commands:
        cid = cmd["command_id"]
        slot_names = get_slot_names(cmd)
        
        vocab = load_vocab(game, cid) if slot_names else {}

        # 获取该 command 的所有别名模板
        if cid in aliases_data:
            templates = aliases_data[cid]["all"]
        else:
            templates = cmd["aliases"]

        if not templates:
            print(f"  ⚠️  {cid}: 无可用模板，跳过")
            continue

        samples = []
        seen_texts = set()

        for _ in range(samples_per_command * 3):  # 多生成以应对去重
            if len(samples) >= samples_per_command:
                break

            tmpl = random.choice(templates)

            if slot_names:
                text, slots_filled = fill_template(tmpl, slot_names, vocab)
            else:
                text = tmpl
                slots_filled = {}

            if text in seen_texts:
                continue
            seen_texts.add(text)

            sample = {
                "text": text,
                "label": "quick_command",
                "command_id": cid,
                "slots": slots_filled,
            }
            samples.append(sample)

        all_samples.extend(samples)
        print(f"  {cid}: {len(samples)} 条模板样本")

    # 保存（按 command_id 分目录）
    by_cmd: dict[str, list[dict]] = {}
    for s in all_samples:
        by_cmd.setdefault(s["command_id"], []).append(s)

    for cid, samples_list in by_cmd.items():
        cmd_dir = os.path.join(PROJECT_DIR, "output", game, cid)
        os.makedirs(cmd_dir, exist_ok=True)
        out_path = os.path.join(cmd_dir, "template.jsonl")
        with open(out_path, "w", encoding="utf-8") as f:
            for s in samples_list:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"\n  ✅ 总计 {len(all_samples)} 条模板样本")
    print(f"     保存到: {out_path}")
    return all_samples


if __name__ == "__main__":
    game = sys.argv[1] if len(sys.argv) > 1 else "mmorpg"
    cid = sys.argv[2] if len(sys.argv) > 2 else None
    generate_template_samples(game, command_id=cid)
