#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
填槽词库生成器
使用 LLM 根据游戏背景与 command 定义生成各命令对应的填槽词库
"""

import json
import os
import sys

from game_context import build_command_semantic_profile, load_game_background
from llm_client import call_llm_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
MAX_TARGETS = 80
MAX_USES = 60
MAX_TARGET_USE_PAIRS = 30
MIN_TARGET_USE_PAIRS = 12


def load_prompt_template() -> str:
    """加载词库生成提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "vocab_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_pair_repair_prompt_template() -> str:
    """加载双槽位 pair 修复提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "pair_repair_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_commands(game: str) -> dict:
    """加载指定游戏的 commands"""
    path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dedupe_strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def normalize_vocab(vocab: object, slot_names: list[str]) -> dict:
    """Normalize LLM output into the stable vocab schema used downstream."""
    if not isinstance(vocab, dict):
        raise ValueError("词库输出不是 JSON 对象")

    targets = _dedupe_strings(vocab.get("targets"))
    uses = _dedupe_strings(vocab.get("uses"))
    target_use_pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    raw_pairs = vocab.get("target_use_pairs", [])
    if isinstance(raw_pairs, list):
        for item in raw_pairs:
            if not isinstance(item, dict):
                continue
            target = item.get("target", "")
            use = item.get("use", "")
            if not isinstance(target, str) or not isinstance(use, str):
                continue
            target = target.strip()
            use = use.strip()
            if not target or not use:
                continue
            if target not in targets:
                targets.append(target)
            if use not in uses:
                uses.append(use)
            pair_key = (target, use)
            if pair_key in seen_pairs:
                continue
            target_use_pairs.append({"target": target, "use": use})
            seen_pairs.add(pair_key)

    if "target" not in slot_names:
        targets = []
    if "use" not in slot_names:
        uses = []
    if not {"target", "use"}.issubset(slot_names):
        target_use_pairs = []
    else:
        target_use_pairs = target_use_pairs[:MAX_TARGET_USE_PAIRS]

    targets = targets[:MAX_TARGETS]
    uses = uses[:MAX_USES]
    target_set = set(targets)
    use_set = set(uses)
    target_use_pairs = [
        pair
        for pair in target_use_pairs
        if pair["target"] in target_set and pair["use"] in use_set
    ]

    return {
        "targets": targets,
        "uses": uses,
        "target_use_pairs": target_use_pairs,
    }


def validate_vocab_for_slots(command_id: str, vocab: dict, slot_names: list[str]) -> None:
    """Validate the normalized vocab against the current command slots."""
    for key in ("targets", "uses", "target_use_pairs"):
        if key not in vocab:
            raise ValueError(f"{command_id} 词库缺少 {key}")

    if "target" in slot_names and not vocab["targets"]:
        raise ValueError(f"{command_id} 需要 target，但词库未生成任何 targets")
    if "use" in slot_names and not vocab["uses"]:
        raise ValueError(f"{command_id} 需要 use，但词库未生成任何 uses")
    if {"target", "use"}.issubset(slot_names) and not vocab["target_use_pairs"]:
        raise ValueError(f"{command_id} 需要 target_use_pairs，但词库未生成任何合法配对")


def repair_target_use_pairs(
    cmd: dict,
    vocab: dict,
    game_background: str,
    repair_template: str,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> list[dict[str, str]]:
    """Use the LLM to recover valid pairs from existing targets/uses only."""
    prompt = repair_template.replace(
        "{{game_background}}",
        game_background or "未提供独立背景文件，请根据 command 文本和语义画像判断。",
    )
    prompt = prompt.replace(
        "{{commands_json}}",
        json.dumps([cmd], ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace(
        "{{command_semantic_profile_json}}",
        json.dumps(build_command_semantic_profile(cmd), ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace(
        "{{targets_json}}",
        json.dumps(vocab["targets"], ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace(
        "{{uses_json}}",
        json.dumps(vocab["uses"], ensure_ascii=False, indent=2),
    )
    prompt = prompt.replace(
        "{{existing_pairs_json}}",
        json.dumps(vocab["target_use_pairs"], ensure_ascii=False, indent=2),
    )

    raw = call_llm_json(
        prompt,
        model=model,
        temperature=0.3,
        max_tokens=8000,
        think_mode=think_mode,
        think_level=think_level,
    )
    if not isinstance(raw, dict):
        raise ValueError("pair 修复结果不是 JSON 对象")

    allowed_targets = set(vocab["targets"])
    allowed_uses = set(vocab["uses"])
    repaired_pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for item in raw.get("target_use_pairs", []):
        if not isinstance(item, dict):
            continue
        target = item.get("target", "")
        use = item.get("use", "")
        if not isinstance(target, str) or not isinstance(use, str):
            continue
        target = target.strip()
        use = use.strip()
        pair_key = (target, use)
        if not target or not use:
            continue
        if target not in allowed_targets or use not in allowed_uses:
            continue
        if pair_key in seen_pairs:
            continue
        repaired_pairs.append({"target": target, "use": use})
        seen_pairs.add(pair_key)
        if len(repaired_pairs) >= MAX_TARGET_USE_PAIRS:
            break

    return repaired_pairs


def merge_target_use_pairs(existing_pairs: list[dict], repaired_pairs: list[dict]) -> list[dict]:
    """Merge original and repaired pairs without duplicates."""
    merged: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for source in (existing_pairs, repaired_pairs):
        for item in source:
            if not isinstance(item, dict):
                continue
            target = item.get("target", "")
            use = item.get("use", "")
            if not isinstance(target, str) or not isinstance(use, str):
                continue
            pair_key = (target, use)
            if pair_key in seen_pairs:
                continue
            merged.append({"target": target, "use": use})
            seen_pairs.add(pair_key)
            if len(merged) >= MAX_TARGET_USE_PAIRS:
                return merged

    return merged


def generate_vocab(
    game: str = "mmorpg",
    command_id: str | None = None,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> dict:
    """
    生成填槽词库

    Args:
        game: 游戏类型
        command_id: 如果指定，只处理该 command
        model: LLM 模型

    Returns:
        按 command_id 索引的词库字典
    """
    print(f"\n{'='*60}")
    print(f"  Step 1: 生成填槽词库 ({game})")
    print(f"{'='*60}")

    commands_data = load_commands(game)
    if "commands" in commands_data:
        commands_list = commands_data["commands"]
    else:
        commands_list = commands_data

    if command_id:
        commands_list = [c for c in commands_list if c["command_id"] == command_id]
        if not commands_list:
            raise ValueError(f"未找到 command_id: {command_id}")

    template = load_prompt_template()
    pair_repair_template = load_pair_repair_prompt_template()
    game_background = load_game_background(game)
    all_vocabs = {}

    for cmd in commands_list:
        cid = cmd["command_id"]
        slots = cmd.get("slots", [])
        slot_names = [slot.get("name") for slot in slots if slot.get("name")]
        
        # 如果没有slots，则不需要生成词库
        if not slots:
            print(f"  [SKIP] {cid}: 无参数，跳过生成词库")
            continue
            
        print(f"  调用 LLM 生成词库 [{cid}]...")
        
        # 只传入当前 command 的定义
        cmd_json = json.dumps([cmd], ensure_ascii=False, indent=2)
        profile = build_command_semantic_profile(cmd)
        prompt = template.replace("{{game_background}}", game_background or "未提供独立背景文件，请主要根据 command 语义推断。")
        prompt = prompt.replace("{{commands_json}}", cmd_json)
        prompt = prompt.replace(
            "{{command_semantic_profile_json}}",
            json.dumps(profile, ensure_ascii=False, indent=2),
        )

        raw_vocab = call_llm_json(
            prompt,
            model=model,
            temperature=0.8,
            max_tokens=12000,
            think_mode=think_mode,
            think_level=think_level,
        )
        vocab = normalize_vocab(raw_vocab, slot_names)

        if {"target", "use"}.issubset(slot_names) and len(vocab["target_use_pairs"]) < MIN_TARGET_USE_PAIRS:
            print(
                f"  [WARN] {cid}: target_use_pairs 数量不足 "
                f"({len(vocab['target_use_pairs'])}/{MIN_TARGET_USE_PAIRS})，调用 LLM 补充配对..."
            )
            repaired_pairs = repair_target_use_pairs(
                cmd,
                vocab,
                game_background,
                pair_repair_template,
                model=model,
                think_mode=think_mode,
                think_level=think_level,
            )
            vocab["target_use_pairs"] = merge_target_use_pairs(
                vocab["target_use_pairs"],
                repaired_pairs,
            )

        validate_vocab_for_slots(cid, vocab, slot_names)

        print(f"  [OK] {cid} 生成完成:")
        print(f"     targets: {len(vocab['targets'])} 个")
        print(f"     uses: {len(vocab['uses'])} 个")
        print(f"     target_use_pairs: {len(vocab['target_use_pairs'])} 对")

        # 独立保存
        out_dir = os.path.join(PROJECT_DIR, "output", game, cid)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "vocab.json")

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(vocab, f, ensure_ascii=False, indent=2)

        print(f"     保存到: {out_path}")
        all_vocabs[cid] = vocab

    return all_vocabs


def load_vocab(game: str = "mmorpg", command_id: str | None = None) -> dict:
    """加载已生成的词库"""
    path = os.path.join(PROJECT_DIR, "output", game, command_id, "vocab.json") if command_id else os.path.join(PROJECT_DIR, "output", game, "vocab.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"词库文件不存在: {path}，请先运行 generate_vocab")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    game = sys.argv[1] if len(sys.argv) > 1 else "mmorpg"
    cid = sys.argv[2] if len(sys.argv) > 2 else None
    generate_vocab(game, command_id=cid)
