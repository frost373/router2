#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Review full-data-check issues one by one and auto-apply safe resolutions.
"""

from __future__ import annotations

import argparse

from full_data_check import run_full_check_issue_review


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="全部数据检查问题样本复核器")
    parser.add_argument("--game", default="mmorpg", help="游戏类型（commands/<game>.json）")
    parser.add_argument("--model", default=None, help="主 LLM 模型名")
    parser.add_argument("--secondary_model", default=None, help="副 LLM 模型名")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="重新开始：清空已有 issue_review 工件后重跑",
    )
    parser.add_argument("--think_mode", action="store_true", help="开启思考模式")
    parser.add_argument("--think_level", default="high", help="思考等级（low/medium/high）")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 条 issue（默认 0 表示全部）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_full_check_issue_review(
        game=args.game,
        model=args.model,
        secondary_model=args.secondary_model,
        restart=args.restart,
        think_mode=args.think_mode,
        think_level=args.think_level,
        limit=args.limit or None,
    )


if __name__ == "__main__":
    main()
