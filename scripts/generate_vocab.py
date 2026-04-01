#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
填槽词库生成器
使用 LLM 根据 commands 定义生成 MMORPG 背景下的各个命令对应的填槽词库
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

    template = load_prompt_template()
    all_vocabs = {}

    for cmd in commands_list:
        cid = cmd["command_id"]
        slots = cmd.get("slots", [])
        
        # 如果没有slots，则不需要生成词库
        if not slots:
            print(f"  [SKIP] {cid}: 无参数，跳过生成词库")
            continue
            
        print(f"  调用 LLM 生成词库 [{cid}]...")
        
        # 只传入当前 command 的定义
        cmd_json = json.dumps([cmd], ensure_ascii=False, indent=2)
        prompt = template.replace("{{commands_json}}", cmd_json)

        vocab = call_llm_json(
            prompt,
            model=model,
            temperature=0.8,
            max_tokens=12000,
            think_mode=think_mode,
            think_level=think_level,
        )

        # 验证格式
        assert "targets" in vocab, f"{cid} 词库缺少 targets"
        assert "uses" in vocab, f"{cid} 词库缺少 uses"
        assert "target_use_pairs" in vocab, f"{cid} 词库缺少 target_use_pairs"

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
