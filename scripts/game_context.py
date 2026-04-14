#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared helpers for game background loading and command semantic inference.
"""

from __future__ import annotations

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

BACKGROUND_FILE_SUFFIX = ".background.txt"

DEFAULT_GAME_BACKGROUNDS = {
    "mmorpg": (
        "这是一款经典的东方玄幻/魔幻MMORPG，包含战士、法师、牧师、射手等职业，"
        "有副本、野外、PVP等场景。玩家可以组队挑战地下城、世界boss、竞技场等。"
        "AI队友是一个可以接受玩家语音/文字命令的智能NPC队友。"
    ),
}

ALLOWED_TARGET_KINDS = {
    "ally",
    "enemy",
    "player",
    "building",
    "landmark",
    "facility",
    "interactive_object",
    "loot",
    "corpse",
    "area",
}
ALLOWED_USE_KINDS = {
    "attack_skill",
    "heal_skill",
    "buff_skill",
    "control_skill",
    "consumable",
    "tool",
    "weapon",
    "weapon_mode",
    "quest_item",
}

TARGET_MODE_NONE = "none"
TARGET_MODE_ENTITY = "entity"
TARGET_MODE_OBJECT = "object"
TARGET_MODE_LOCATION = "location"
TARGET_MODE_MIXED = "mixed"

USE_MODE_NONE = "none"
USE_MODE_SKILL_OR_ITEM = "skill_or_item"

TARGET_KIND_LABELS = {
    "ally": "友方队友或需支援的友军",
    "enemy": "敌方单位、威胁目标或需要压制/攻击的对象",
    "player": "玩家本人或玩家所在位置",
    "building": "建筑物、楼体、营地、房屋、塔楼、据点",
    "landmark": "地图地标、入口、路口、高地、桥头、祭坛等标志性地点",
    "facility": "功能设施、固定装置、炮台、水晶、终端、补给站等",
    "interactive_object": "可交互物件、机关、NPC、门、箱子、控制台、开关",
    "loot": "掉落物、战利品、装备、材料、任务物品、补给物",
    "corpse": "尸体、倒地单位、可搜刮的遗体",
    "area": "房间、区域、掩体点、站位点、路线或完整地点短语",
}

USE_KIND_LABELS = {
    "attack_skill": "攻击技能、法术、伤害型动作或进攻型能力",
    "heal_skill": "治疗、复活、修复、扶起类技能或能力",
    "buff_skill": "增益、护盾、加速、强化、防护类技能或能力",
    "control_skill": "控制、打断、沉默、压制、限制行动类技能或能力",
    "consumable": "可立即消耗或投掷使用的药剂、卷轴、手雷、炸弹、补给品",
    "tool": "工具、装置、破解器、钥匙、任务工具、交互工具",
    "weapon": "武器本体、武器类别或具名武器",
    "weapon_mode": "武器模式、射击模式、弹药模式、架设模式等",
    "quest_item": "任务道具、关键物品、芯片、信标、核心等特殊物品",
}

_MOVE_KEYWORDS = (
    "移动", "前往", "去", "过去", "到", "前进", "靠近", "上到", "赶到",
    "抵达", "回到", "撤到", "靠过去", "去到",
)
_LOCATION_KEYWORDS = (
    "位置", "地点", "区域", "点位", "房间", "门口", "路口", "高点", "高台",
    "楼顶", "楼下", "二楼", "桥头", "掩体", "阵地", "入口", "出口", "附近",
    "这里", "那里", "那边", "左边", "右边", "后面", "前面",
)
_LOOT_KEYWORDS = (
    "拾取", "拿取", "捡", "捡起", "搜刮", "搜", "摸尸", "拿过来", "拿回来",
    "取回", "拿上", "拾", "补给", "物资", "战利品", "掉落", "掉的",
)
_INTERACT_KEYWORDS = (
    "交互", "操作", "开启", "打开", "激活", "启动", "使用机关", "触发", "点击",
    "点开", "点一下", "点{", "开一下", "弄一下", "接一下", "处理一下", "操作一下",
    "破解", "解锁",
)
_ATTACK_KEYWORDS = (
    "攻击", "打", "杀", "干掉", "点掉", "揍", "输出", "开火", "集火", "轰",
    "压制", "扫", "射", "火力",
)
_CONTROL_KEYWORDS = (
    "控制", "控", "定住", "晕", "沉默", "禁锢", "打断", "断", "压制", "牵制",
)
_HEAL_KEYWORDS = (
    "治疗", "抬", "奶", "回", "回复", "救", "扶", "拉起", "复活", "修复",
)
_BUFF_KEYWORDS = (
    "增益", "buff", "护盾", "加速", "强化", "上状态", "祝福", "防护", "掩护",
)
_DEFEND_KEYWORDS = (
    "防守", "守住", "驻守", "看住", "守", "保护", "掩护", "盯住",
)
_SELF_KEYWORDS = ("自己", "自身", "自用", "先吃", "给自己", "self")
_WEAPON_KEYWORDS = (
    "武器", "枪", "炮", "榴弹", "导弹", "火箭", "狙", "弹药", "模式", "架枪",
)
_TOOL_KEYWORDS = (
    "工具", "装置", "破解器", "钥匙", "终端", "信标", "无人机", "炮台", "陷阱",
    "地雷", "黑客", "工程", "修理",
)
_CONSUMABLE_KEYWORDS = (
    "药", "药水", "注射器", "医疗包", "包", "卷轴", "手雷", "炸弹", "补给",
    "瓶", "剂", "恢复", "急救", "绷带",
)
_OBJECT_KEYWORDS = (
    "宝箱", "箱子", "门", "机关", "控制台", "终端", "开关", "水晶", "祭坛",
    "炮台", "NPC", "传送门", "信标", "路障", "电梯", "柜", "按钮",
)
_BUILDING_KEYWORDS = (
    "建筑", "楼", "房", "塔", "城门", "营地", "仓库", "旅店", "哨塔", "据点",
    "基地", "神殿", "教堂", "堡垒", "工厂",
)


def _merge_unique(values: list[str], extra: list[str]) -> list[str]:
    seen = set(values)
    for value in extra:
        if value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _build_prompt_descriptions(values: list[str], label_map: dict[str, str]) -> list[dict]:
    return [{"kind": value, "description": label_map[value]} for value in values]


def _normalize_hint_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if text:
                result.append(text)
    return result


def get_game_background_path(game: str) -> str:
    return os.path.join(PROJECT_DIR, "commands", f"{game}{BACKGROUND_FILE_SUFFIX}")


def load_game_background(game: str) -> str:
    """Load the preferred background text for one game."""
    preferred = get_game_background_path(game)
    if os.path.isfile(preferred):
        with open(preferred, "r", encoding="utf-8") as f:
            return f.read().strip()

    game_dir = os.path.join(PROJECT_DIR, "output", game)
    txt_path = os.path.join(game_dir, "game_background.txt")
    if os.path.isfile(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    json_path = os.path.join(game_dir, "audit_context.json")
    if os.path.isfile(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            value = data.get("game_background", "")
            if isinstance(value, str) and value.strip():
                return value.strip()

    return DEFAULT_GAME_BACKGROUNDS.get(game, "")


def discover_game_background(game: str) -> str:
    """Backward-compatible alias used by existing scripts."""
    return load_game_background(game)


def build_command_semantic_profile(cmd: dict) -> dict:
    """Infer vocabulary guidance from command text instead of hardcoded ids."""
    slot_names = [slot.get("name") for slot in cmd.get("slots", []) if slot.get("name")]
    text_parts = [str(cmd.get("desc", ""))]
    text_parts.extend(alias for alias in cmd.get("aliases", []) if isinstance(alias, str))
    analysis_text = " ".join(text_parts).lower()

    hints = cmd.get("vocab_hints") if isinstance(cmd.get("vocab_hints"), dict) else {}
    target_kinds: list[str] = []
    use_kinds: list[str] = []
    action_tags: list[str] = []

    is_move = _contains_any(analysis_text, _MOVE_KEYWORDS)
    is_location = _contains_any(analysis_text, _LOCATION_KEYWORDS)
    is_loot = _contains_any(analysis_text, _LOOT_KEYWORDS)
    is_interact = _contains_any(analysis_text, _INTERACT_KEYWORDS)
    is_attack = _contains_any(analysis_text, _ATTACK_KEYWORDS)
    is_control = _contains_any(analysis_text, _CONTROL_KEYWORDS)
    is_heal = _contains_any(analysis_text, _HEAL_KEYWORDS)
    is_buff = _contains_any(analysis_text, _BUFF_KEYWORDS)
    is_defend = _contains_any(analysis_text, _DEFEND_KEYWORDS)
    is_self = _contains_any(analysis_text, _SELF_KEYWORDS)
    is_weapon = _contains_any(analysis_text, _WEAPON_KEYWORDS)
    is_tool = _contains_any(analysis_text, _TOOL_KEYWORDS)
    is_consumable = _contains_any(analysis_text, _CONSUMABLE_KEYWORDS)
    is_object = _contains_any(analysis_text, _OBJECT_KEYWORDS)
    is_building = _contains_any(analysis_text, _BUILDING_KEYWORDS)

    if is_move:
        action_tags.append("move")
    if is_loot:
        action_tags.append("loot")
    if is_interact:
        action_tags.append("interact")
    if is_attack:
        action_tags.append("attack")
    if is_control:
        action_tags.append("control")
    if is_heal:
        action_tags.append("heal")
    if is_buff:
        action_tags.append("buff")
    if is_defend:
        action_tags.append("defend")
    if is_self:
        action_tags.append("self")

    if "target" not in slot_names:
        target_mode = TARGET_MODE_NONE
    elif is_loot:
        target_mode = TARGET_MODE_OBJECT
        _merge_unique(target_kinds, ["loot", "corpse", "interactive_object", "facility"])
    elif is_interact:
        target_mode = TARGET_MODE_OBJECT if not (is_move or is_location) else TARGET_MODE_MIXED
        _merge_unique(target_kinds, ["interactive_object", "facility", "building", "landmark"])
    elif is_move or is_location:
        target_mode = TARGET_MODE_LOCATION
        _merge_unique(target_kinds, ["area", "landmark", "building", "facility"])
    elif is_heal or is_buff:
        target_mode = TARGET_MODE_ENTITY
        _merge_unique(target_kinds, ["ally", "player"])
    elif is_defend:
        target_mode = TARGET_MODE_MIXED
        _merge_unique(target_kinds, ["ally", "player", "building", "facility", "landmark", "area"])
    elif is_attack or is_control:
        target_mode = TARGET_MODE_ENTITY
        _merge_unique(target_kinds, ["enemy"])
    else:
        target_mode = TARGET_MODE_MIXED
        _merge_unique(target_kinds, ["enemy", "ally", "player", "interactive_object", "area"])

    if is_object:
        _merge_unique(target_kinds, ["interactive_object", "facility"])
    if is_building:
        _merge_unique(target_kinds, ["building", "landmark"])

    if "use" not in slot_names:
        use_mode = USE_MODE_NONE
    else:
        use_mode = USE_MODE_SKILL_OR_ITEM
        if is_heal:
            _merge_unique(use_kinds, ["heal_skill", "consumable"])
        if is_buff or is_self or is_defend:
            _merge_unique(use_kinds, ["buff_skill", "consumable", "tool"])
        if is_control:
            _merge_unique(use_kinds, ["control_skill", "tool"])
        if is_attack:
            _merge_unique(use_kinds, ["attack_skill", "weapon", "consumable"])
        if is_weapon:
            _merge_unique(use_kinds, ["weapon", "weapon_mode"])
        if is_tool:
            _merge_unique(use_kinds, ["tool", "quest_item"])
        if is_consumable:
            _merge_unique(use_kinds, ["consumable"])

        if not use_kinds:
            _merge_unique(use_kinds, ["attack_skill", "buff_skill", "consumable", "tool"])

    hint_target_kinds = [
        item for item in _normalize_hint_list(hints.get("target_kinds"))
        if item in ALLOWED_TARGET_KINDS
    ]
    hint_use_kinds = [
        item for item in _normalize_hint_list(hints.get("use_kinds"))
        if item in ALLOWED_USE_KINDS
    ]
    pairing_notes = _normalize_hint_list(hints.get("pairing_notes"))

    _merge_unique(target_kinds, hint_target_kinds)
    _merge_unique(use_kinds, hint_use_kinds)

    if "target" in slot_names and "use" in slot_names:
        if target_mode == TARGET_MODE_ENTITY and target_kinds == ["enemy"]:
            pairing_notes.append("target_use_pairs 只生成敌方目标与攻击/控制/武器/投掷物的合理组合。")
        elif target_mode == TARGET_MODE_ENTITY and set(target_kinds).issubset({"ally", "player"}):
            pairing_notes.append("target_use_pairs 只生成友方目标与治疗/增益/防护/复活类 use 的合理组合。")
        elif target_mode in {TARGET_MODE_OBJECT, TARGET_MODE_LOCATION}:
            pairing_notes.append("target_use_pairs 只生成场景对象或地点与工具/装置/消耗品的合理组合。")
        else:
            pairing_notes.append("target_use_pairs 要严格服从该命令描述，不要混入背景里无关的目标或 use。")
    elif "target" in slot_names:
        pairing_notes.append("当前命令只需要 target；uses 与 target_use_pairs 可以为空。")
    elif "use" in slot_names:
        pairing_notes.append("当前命令只需要 use；targets 与 target_use_pairs 可以为空。")

    if not target_kinds and "target" in slot_names:
        target_kinds = ["enemy", "ally", "interactive_object", "area"]
        target_mode = TARGET_MODE_MIXED
    if not use_kinds and "use" in slot_names:
        use_kinds = ["attack_skill", "consumable", "tool"]

    return {
        "slot_names": slot_names,
        "target_mode": target_mode,
        "target_kinds": target_kinds,
        "target_kind_descriptions": _build_prompt_descriptions(target_kinds, TARGET_KIND_LABELS),
        "use_mode": use_mode,
        "use_kinds": use_kinds,
        "use_kind_descriptions": _build_prompt_descriptions(use_kinds, USE_KIND_LABELS),
        "action_tags": action_tags,
        "pairing_notes": pairing_notes,
        "vocab_hints": hints if hints else {},
    }
