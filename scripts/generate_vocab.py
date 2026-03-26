#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
填槽词库生成器
使用 LLM 根据 commands 定义生成 MMORPG 背景下的填槽词库
"""

import json
import os
import sys

from llm_client import call_llm_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)


def load_prompt_template() -> str:
    """加载词库生成提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "vocab_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_commands(game: str) -> dict:
    """加载指定游戏的 commands"""
    path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_vocab(game: str = "mmorpg", model: str | None = None) -> dict:
    """
    生成填槽词库

    Args:
        game: 游戏类型
        model: LLM 模型

    Returns:
        词库字典 {"targets": [...], "uses": [...], "target_use_pairs": [...]}
    """
    print(f"\n{'='*60}")
    print(f"  Step 1: 生成填槽词库 ({game})")
    print(f"{'='*60}")

    commands_data = load_commands(game)
    template = load_prompt_template()

    # 构建 prompt
    commands_json = json.dumps(commands_data, ensure_ascii=False, indent=2)
    prompt = template.replace("{{commands_json}}", commands_json)

    print(f"  调用 LLM 生成词库...")
    vocab = call_llm_json(prompt, model=model, temperature=0.8)

    # 验证格式
    assert "targets" in vocab, "词库缺少 targets"
    assert "uses" in vocab, "词库缺少 uses"
    assert "target_use_pairs" in vocab, "词库缺少 target_use_pairs"

    print(f"  ✅ 生成完成:")
    print(f"     targets: {len(vocab['targets'])} 个")
    print(f"     uses: {len(vocab['uses'])} 个")
    print(f"     target_use_pairs: {len(vocab['target_use_pairs'])} 对")

    # 保存
    out_dir = os.path.join(PROJECT_DIR, "output", game)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "vocab.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False, indent=2)

    print(f"     保存到: {out_path}")
    return vocab


def load_vocab(game: str = "mmorpg") -> dict:
    """加载已生成的词库"""
    path = os.path.join(PROJECT_DIR, "output", game, "vocab.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"词库文件不存在: {path}，请先运行 generate_vocab")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    game = sys.argv[1] if len(sys.argv) > 1 else "mmorpg"
    generate_vocab(game)
