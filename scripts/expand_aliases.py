#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
别名模板扩写器
使用 LLM 对每个 command 的 aliases 进行扩写
"""

import json
import os
import sys

from game_context import build_command_semantic_profile, load_game_background
from llm_client import call_llm_json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)


def load_prompt_template() -> str:
    """加载别名扩写提示词模板"""
    path = os.path.join(SCRIPT_DIR, "prompts", "alias_expand_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_commands(game: str) -> list[dict]:
    """加载指定游戏的 commands 列表"""
    path = os.path.join(PROJECT_DIR, "commands", f"{game}.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["commands"]


def expand_aliases_for_command(
    cmd: dict,
    template: str,
    game_background: str,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> list[str]:
    """
    对单个 command 扩写别名

    Returns:
        新增的别名列表
    """
    prompt = template.replace("{{command_id}}", cmd["command_id"])
    prompt = prompt.replace("{{desc}}", cmd["desc"])
    prompt = prompt.replace(
        "{{slots}}", json.dumps(cmd["slots"], ensure_ascii=False)
    )
    prompt = prompt.replace(
        "{{aliases}}", json.dumps(cmd["aliases"], ensure_ascii=False)
    )
    prompt = prompt.replace(
        "{{game_background}}",
        game_background or "未提供独立背景文件，请根据 command 文本推断玩家表达风格。",
    )
    prompt = prompt.replace(
        "{{command_semantic_profile_json}}",
        json.dumps(build_command_semantic_profile(cmd), ensure_ascii=False, indent=2),
    )

    result = call_llm_json(
        prompt,
        model=model,
        temperature=0.8,
        max_tokens=12000,
        think_mode=think_mode,
        think_level=think_level,
    )

    new_aliases = result.get("new_aliases", [])

    # 去重：移除与原始 aliases 重复的
    original = set(cmd["aliases"])
    new_aliases = [a for a in new_aliases if a not in original]

    return new_aliases


def expand_aliases(
    game: str = "mmorpg",
    command_id: str | None = None,
    model: str | None = None,
    think_mode: bool = False,
    think_level: str = "high",
) -> dict:
    """
    对 commands 进行别名扩写

    Args:
        game: 游戏类型
        command_id: 如果指定，只扩写该 command
        model: LLM 模型

    Returns:
        {command_id: {"original": [...], "expanded": [...]}}
    """
    print(f"\n{'='*60}")
    print(f"  Step 2: 别名模板扩写 ({game})")
    print(f"{'='*60}")

    commands = load_commands(game)
    template = load_prompt_template()
    game_background = load_game_background(game)

    # 过滤
    if command_id:
        commands = [c for c in commands if c["command_id"] == command_id]
        if not commands:
            raise ValueError(f"未找到 command_id: {command_id}")

    result = {}
    for cmd in commands:
        cid = cmd["command_id"]
        print(f"\n  扩写 {cid}...")

        new_aliases = expand_aliases_for_command(
            cmd,
            template,
            game_background,
            model=model,
            think_mode=think_mode,
            think_level=think_level,
        )

        all_aliases = cmd["aliases"] + new_aliases
        result[cid] = {
            "original": cmd["aliases"],
            "expanded": new_aliases,
            "all": all_aliases,
        }

        print(f"    原始: {len(cmd['aliases'])} 条")
        print(f"    新增: {len(new_aliases)} 条")
        print(f"    总计: {len(all_aliases)} 条")

    # 保存（每个 command 存到自己的目录）
    for cid, data in result.items():
        cmd_dir = os.path.join(PROJECT_DIR, "output", game, cid)
        os.makedirs(cmd_dir, exist_ok=True)
        out_path = os.path.join(cmd_dir, "aliases.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({cid: data}, f, ensure_ascii=False, indent=2)
        print(f"  [OK] {cid} 保存到: {out_path}")
    return result


def load_expanded_aliases(
    game: str = "mmorpg", command_id: str | None = None
) -> dict:
    """加载已扩写的别名"""
    result = {}
    game_dir = os.path.join(PROJECT_DIR, "output", game)

    if command_id:
        # 加载单个 command
        path = os.path.join(game_dir, command_id, "aliases.json")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"扩写别名文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            result.update(json.load(f))
    else:
        # 加载所有 command
        if not os.path.isdir(game_dir):
            raise FileNotFoundError(f"输出目录不存在: {game_dir}")
        for cid_dir in os.listdir(game_dir):
            alias_path = os.path.join(game_dir, cid_dir, "aliases.json")
            if os.path.isfile(alias_path):
                with open(alias_path, "r", encoding="utf-8") as f:
                    result.update(json.load(f))

    return result


if __name__ == "__main__":
    game = sys.argv[1] if len(sys.argv) > 1 else "mmorpg"
    cid = sys.argv[2] if len(sys.argv) > 2 else None
    expand_aliases(game, command_id=cid)
