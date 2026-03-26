#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
command_registry_compact — Commands 注册表压缩器
将 commands JSON 压缩为简洁的文本格式，用于注入到 LLM 提示词中以减少 token 消耗。

输出格式（每行一条指令）：
    COMMAND_ID|精简描述|alias1/alias2/alias3/...
"""

import json
import re
import sys
import os
import argparse

# ── 需要从 desc 中去除的前缀 ──────────────────────────────
_DESC_PREFIXES = [
    "命令AI队友",
    "命令队友",
    "命令ai队友",
]


def load_commands(filepath: str) -> list[dict]:
    """加载 commands JSON 文件，返回 commands 列表。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["commands"]


def compact_desc(desc: str) -> str:
    """精简描述文本：去除常见前缀。"""
    for prefix in _DESC_PREFIXES:
        if desc.startswith(prefix):
            return desc[len(prefix):]
    return desc


def compact_command(cmd: dict) -> str:
    """将单条指令压缩为 ID|desc|aliases 格式的一行文本。"""
    command_id = cmd["command_id"]
    desc = compact_desc(cmd["desc"])
    aliases = "/".join(cmd["aliases"])
    return f"{command_id}|{desc}|{aliases}"


def compact_registry(commands: list[dict]) -> str:
    """将整个 commands 列表压缩为多行文本。"""
    lines = [compact_command(cmd) for cmd in commands]
    return "\n".join(lines)


def compact_file(filepath: str) -> str:
    """从文件加载并压缩，返回压缩后的文本字符串。"""
    commands = load_commands(filepath)
    return compact_registry(commands)


# ── CLI 入口 ──────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="将 commands JSON 压缩为 LLM 提示词注入格式"
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="commands JSON 文件路径（默认: commands/ 下所有 .json）",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出文件路径（默认: 输出到终端）",
    )
    args = parser.parse_args()

    # 确定输入文件
    if args.input:
        files = [args.input]
    else:
        cmd_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "commands"
        )
        files = [
            os.path.join(cmd_dir, f)
            for f in os.listdir(cmd_dir)
            if f.endswith(".json")
        ]
        if not files:
            print("未找到任何 json 文件")
            sys.exit(1)

    # 处理每个文件
    results = []
    for fp in files:
        if not os.path.isfile(fp):
            print(f"❌ 文件不存在: {fp}", file=sys.stderr)
            sys.exit(1)
        result = compact_file(fp)
        results.append(result)

    output_text = "\n".join(results)

    # 输出
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"✅ 已输出到 {args.output}（{len(output_text)} 字符）")
    else:
        print(output_text)
