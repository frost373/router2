#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
commands JSON 校验器
根据《指令主表生成提示词》设计文档校验 command registry 的合法性
"""

import json
import re
import sys
import os

from game_context import ALLOWED_TARGET_KINDS, ALLOWED_USE_KINDS

# ── 规则常量 ──────────────────────────────────────────────
ALLOWED_SLOT_NAMES = {"use", "target"}
ALLOWED_SLOT_TYPES = {"use_span", "target_span"}
SLOT_NAME_TYPE_MAP = {"use": "use_span", "target": "target_span"}
REQUIRED_FIELDS = {"command_id", "desc", "slots", "aliases"}
COMMAND_ID_PATTERN = re.compile(r"^[A-Z][A-Z_]*[A-Z]$|^[A-Z]+$")
MIN_COMMANDS = 15
MAX_COMMANDS = 25
MIN_ALIASES = 3
MAX_ALIASES = 6
MAX_SLOTS = 2


def _validate_hint_string_list(
    cmd_prefix: str,
    field_name: str,
    raw_value: object,
    allowed_values: set[str] | None,
    errors: list[str],
):
    if not isinstance(raw_value, list):
        errors.append(f"{cmd_prefix}.{field_name}: 必须是字符串数组")
        return

    for idx, item in enumerate(raw_value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{cmd_prefix}.{field_name}[{idx}]: 必须是非空字符串")
            continue
        if allowed_values is not None and item not in allowed_values:
            errors.append(
                f"{cmd_prefix}.{field_name}[{idx}]: 值 '{item}' 非法，"
                f"只允许 {sorted(allowed_values)}"
            )


def validate_file(filepath: str):
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. 读取 JSON ──
    if not os.path.isfile(filepath):
        print(f"[ERROR] 文件不存在: {filepath}")
        return False

    with open(filepath, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON 解析失败: {e}")
            return False

    # ── 2. 根结构校验 ──
    if not isinstance(data, dict) or "commands" not in data:
        errors.append("根对象必须包含 'commands' 数组")
        _print_result(errors, warnings, filepath)
        return False

    commands = data["commands"]
    if not isinstance(commands, list):
        errors.append("'commands' 必须是数组")
        _print_result(errors, warnings, filepath)
        return False

    # ── 3. 数量校验 ──
    count = len(commands)
    if count < MIN_COMMANDS or count > MAX_COMMANDS:
        warnings.append(
            f"command 数量为 {count}，建议范围 {MIN_COMMANDS}-{MAX_COMMANDS}"
        )

    # ── 4. 逐条校验 ──
    seen_ids: set[str] = set()

    for idx, cmd in enumerate(commands):
        prefix = f"commands[{idx}]"

        # 4.1 必要字段
        missing = REQUIRED_FIELDS - set(cmd.keys())
        if missing:
            errors.append(f"{prefix}: 缺少必要字段 {missing}")
            continue

        # 4.2 command_id 格式
        cid = cmd["command_id"]
        if not isinstance(cid, str) or not COMMAND_ID_PATTERN.match(cid):
            errors.append(
                f"{prefix}: command_id '{cid}' 格式非法，"
                "应为全大写字母+下划线且不含 CMD_ 前缀"
            )
        if cid.startswith("CMD_"):
            errors.append(f"{prefix}: command_id 不应使用 CMD_ 前缀")
        if cid in seen_ids:
            errors.append(f"{prefix}: command_id '{cid}' 重复")
        seen_ids.add(cid)

        # 4.3 desc
        desc = cmd["desc"]
        if not isinstance(desc, str) or len(desc) == 0:
            errors.append(f"{prefix}: desc 不能为空")

        # 4.4 slots 校验
        slots = cmd["slots"]
        if not isinstance(slots, list):
            errors.append(f"{prefix}: slots 必须是数组")
        else:
            if len(slots) > MAX_SLOTS:
                errors.append(
                    f"{prefix}: slots 最多 {MAX_SLOTS} 个，当前 {len(slots)}"
                )

            slot_names_in_cmd: set[str] = set()
            for si, slot in enumerate(slots):
                sp = f"{prefix}.slots[{si}]"

                # 必要字段
                for k in ("name", "required", "type"):
                    if k not in slot:
                        errors.append(f"{sp}: 缺少字段 '{k}'")

                # name 合法性
                sname = slot.get("name")
                if sname not in ALLOWED_SLOT_NAMES:
                    errors.append(
                        f"{sp}: slot name '{sname}' 非法，"
                        f"只允许 {ALLOWED_SLOT_NAMES}"
                    )
                if sname in slot_names_in_cmd:
                    errors.append(f"{sp}: slot name '{sname}' 在同一 command 中重复")
                slot_names_in_cmd.add(sname)

                # type 合法性
                stype = slot.get("type")
                if stype not in ALLOWED_SLOT_TYPES:
                    errors.append(
                        f"{sp}: slot type '{stype}' 非法，"
                        f"只允许 {ALLOWED_SLOT_TYPES}"
                    )

                # name-type 一致性
                if sname in SLOT_NAME_TYPE_MAP and stype != SLOT_NAME_TYPE_MAP[sname]:
                    errors.append(
                        f"{sp}: slot name '{sname}' 应对应 type "
                        f"'{SLOT_NAME_TYPE_MAP[sname]}'，实际为 '{stype}'"
                    )

                # 禁止 multi 字段
                if "multi" in slot:
                    errors.append(f"{sp}: 不应包含 'multi' 字段")

                # required 类型
                if "required" in slot and not isinstance(slot["required"], bool):
                    errors.append(f"{sp}: 'required' 字段应为 bool")

            # 双参数时必须是 use + target
            if len(slots) == 2:
                names = {s.get("name") for s in slots}
                if names != {"use", "target"}:
                    errors.append(
                        f"{prefix}: 双参数 command 的 slots 必须是 use + target"
                    )

        # 4.5 aliases 校验
        aliases = cmd["aliases"]
        if not isinstance(aliases, list):
            errors.append(f"{prefix}: aliases 必须是数组")
        else:
            if len(aliases) < MIN_ALIASES or len(aliases) > MAX_ALIASES:
                warnings.append(
                    f"{prefix} ({cid}): aliases 数量为 {len(aliases)}，"
                    f"建议范围 {MIN_ALIASES}-{MAX_ALIASES}"
                )

            # aliases 中的占位符要和 slots 一致
            alias_slots_used: set[str] = set()
            for ai, alias in enumerate(aliases):
                placeholders = set(re.findall(r"\{(\w+)\}", alias))
                alias_slots_used.update(placeholders)

                # 占位符必须是合法 slot name
                for ph in placeholders:
                    if ph not in ALLOWED_SLOT_NAMES:
                        errors.append(
                            f"{prefix}.aliases[{ai}]: 占位符 '{{{ph}}}' "
                            f"使用了非法 slot name"
                        )

            # 有参数 command 的 aliases 应使用对应占位符
            defined_slot_names = {s.get("name") for s in slots} if isinstance(slots, list) else set()
            if defined_slot_names and not alias_slots_used:
                warnings.append(
                    f"{prefix} ({cid}): 定义了 slots {defined_slot_names} "
                    f"但 aliases 中未使用任何占位符"
                )
            # aliases 中用了占位符但 slots 没定义
            extra = alias_slots_used - defined_slot_names
            if extra:
                errors.append(
                    f"{prefix} ({cid}): aliases 中使用了未定义的 slot: {extra}"
                )

        # 4.6 vocab_hints 可选校验
        if "vocab_hints" in cmd:
            vocab_hints = cmd["vocab_hints"]
            if not isinstance(vocab_hints, dict):
                errors.append(f"{prefix}.vocab_hints: 必须是对象")
            else:
                for key in vocab_hints.keys():
                    if key not in {"target_kinds", "use_kinds", "pairing_notes"}:
                        errors.append(
                            f"{prefix}.vocab_hints: 不支持字段 '{key}'，"
                            "只允许 target_kinds/use_kinds/pairing_notes"
                        )

                if "target_kinds" in vocab_hints:
                    _validate_hint_string_list(
                        prefix,
                        "vocab_hints.target_kinds",
                        vocab_hints["target_kinds"],
                        ALLOWED_TARGET_KINDS,
                        errors,
                    )
                if "use_kinds" in vocab_hints:
                    _validate_hint_string_list(
                        prefix,
                        "vocab_hints.use_kinds",
                        vocab_hints["use_kinds"],
                        ALLOWED_USE_KINDS,
                        errors,
                    )
                if "pairing_notes" in vocab_hints:
                    _validate_hint_string_list(
                        prefix,
                        "vocab_hints.pairing_notes",
                        vocab_hints["pairing_notes"],
                        None,
                        errors,
                    )

    # ── 5. 全局去重检查（command_id 已在上面做过） ──

    # ── 6. 统计 & 输出 ──
    _print_result(errors, warnings, filepath)
    _print_statistics(commands)
    _print_command_details(commands)

    return len(errors) == 0


def _print_result(errors: list[str], warnings: list[str], filepath: str):
    print("=" * 60)
    print(f"  校验文件: {filepath}")
    print("=" * 60)

    if warnings:
        print(f"\n[WARN] 警告 ({len(warnings)}):")
        for w in warnings:
            print(f"   [WARN] {w}")

    if errors:
        print(f"\n[ERROR] 错误 ({len(errors)}):")
        for e in errors:
            print(f"   [ERROR] {e}")
        print(f"\n校验结果: FAILED (共 {len(errors)} 个错误)")
    else:
        print(f"\n校验结果: PASSED")


def _print_statistics(commands: list[dict]):
    print("\n" + "=" * 60)
    print("  统计信息")
    print("=" * 60)

    total = len(commands)
    no_param = sum(1 for c in commands if len(c.get("slots", [])) == 0)
    has_param = total - no_param
    single_param = sum(1 for c in commands if len(c.get("slots", [])) == 1)
    dual_param = sum(1 for c in commands if len(c.get("slots", [])) == 2)

    print(f"\n  总 command 数量: {total}")
    print(f"\n  [STATS] 无参数 vs 有参数:")
    print(f"     无参数: {no_param} ({no_param/total*100:.1f}%)")
    print(f"     有参数: {has_param} ({has_param/total*100:.1f}%)")

    print(f"\n  [STATS] 单参数 vs 双参数 (仅统计有参数的 {has_param} 个):")
    print(f"     单参数: {single_param} ({single_param/has_param*100:.1f}%)" if has_param else "     单参数: 0")
    print(f"     双参数: {dual_param} ({dual_param/has_param*100:.1f}%)" if has_param else "     双参数: 0")

    # slot name 分布
    target_only = sum(
        1 for c in commands
        if len(c.get("slots", [])) == 1 and c["slots"][0].get("name") == "target"
    )
    use_only = sum(
        1 for c in commands
        if len(c.get("slots", [])) == 1 and c["slots"][0].get("name") == "use"
    )
    print(f"\n  [STATS] 参数类型分布:")
    print(f"     仅 target:      {target_only}")
    print(f"     仅 use:         {use_only}")
    print(f"     use + target:   {dual_param}")
    print(f"     无参数:         {no_param}")


def _print_command_details(commands: list[dict]):
    print("\n" + "=" * 60)
    print("  指令详情")
    print("=" * 60)

    for i, cmd in enumerate(commands):
        cid = cmd.get("command_id", "???")
        desc = cmd.get("desc", "")
        slots = cmd.get("slots", [])
        aliases = cmd.get("aliases", [])

        # slots 文本化
        if not slots:
            slots_text = "(无参数)"
        else:
            parts = []
            for s in slots:
                parts.append(f"{s['name']}:{s['type']}")
            slots_text = ", ".join(parts)

        print(f"\n  [{i+1:02d}] {cid}")
        print(f"       desc:    {desc}")
        print(f"       slots:   {slots_text}")
        print(f"       aliases: {' | '.join(aliases)}")


# ── 入口 ──────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 默认校验 commands 目录下所有 json
        cmd_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "commands")
        files = [
            os.path.join(cmd_dir, f)
            for f in os.listdir(cmd_dir)
            if f.endswith(".json")
        ]
        if not files:
            print("未找到任何 json 文件")
            sys.exit(1)
    else:
        files = sys.argv[1:]

    all_ok = True
    for fp in files:
        ok = validate_file(fp)
        if not ok:
            all_ok = False
        print()

    sys.exit(0 if all_ok else 1)
