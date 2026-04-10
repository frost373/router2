#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练数据生成器 — Dashboard 后端服务
FastAPI + SSE 实时推送
"""

import asyncio
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# ── 路径常量 ────────────────────────────────────────────
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DASHBOARD_DIR.parent.parent          # router2/
SCRIPTS_DIR = PROJECT_DIR / "scripts"
COMMANDS_DIR = PROJECT_DIR / "commands"
OUTPUT_DIR = PROJECT_DIR / "output"
LLM_CONFIG = PROJECT_DIR / "LLM.txt"
LLM_LOG_PATH = PROJECT_DIR / "logs" / "llm_interaction.log"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from full_data_check import (  # noqa: E402
    DEFAULT_BATCH_SIZE as DEFAULT_FULL_CHECK_BATCH_SIZE,
    apply_full_check_actions,
    get_full_check_issues,
    get_full_check_overview,
)

# ── FastAPI 应用 ────────────────────────────────────────
app = FastAPI(title="训练数据生成器 Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── 生成任务状态 ─────────────────────────────────────────

# 步骤定义（关键字 → 显示名）
MAIN_PIPELINE_STEPS = [
    {"key": "Step 1", "name": "词库生成"},
    {"key": "Step 2", "name": "别名扩写"},
    {"key": "Step 3", "name": "模板填槽"},
    {"key": "Step 4", "name": "对抗样本"},
    {"key": "Step 5:", "name": "Paraphrase"},
    {"key": "Step 6", "name": "合并输出"},
    {"key": "Step 7", "name": "质量抽查"},
]

GLOBAL_NEGATIVE_STEPS = [
    {"name": "全局负样本生成"},
]
QUALITY_AUDIT_STEPS = [
    {"name": "璐ㄩ噺鎶芥煡"},
]
FULL_DATA_CHECK_STEPS = [
    {"key": "Step 1", "name": "构建快照"},
    {"key": "Step 2", "name": "全量检查"},
    {"key": "Step 3", "name": "汇总结果"},
]
FULL_CHECK_REVIEW_STEPS = [
    {"key": "Step 1", "name": "准备复核"},
    {"key": "Step 2", "name": "逐条复核"},
    {"key": "Step 3", "name": "汇总结果"},
]


def normalize_steps(steps: list[dict]) -> list[dict]:
    """Normalize step definitions to dashboard-friendly shape."""
    return [
        {
            "index": idx,
            "name": step["name"],
            "key": step.get("key"),
        }
        for idx, step in enumerate(steps)
    ]


def get_llm_log_size() -> int:
    try:
        return LLM_LOG_PATH.stat().st_size if LLM_LOG_PATH.exists() else 0
    except OSError:
        return 0


def read_llm_log_chunk(from_pos: int = 0) -> tuple[str, int]:
    if from_pos < 0:
        from_pos = 0
    if not LLM_LOG_PATH.exists():
        return "", 0

    try:
        with open(LLM_LOG_PATH, "r", encoding="utf-8") as f:
            f.seek(from_pos)
            new_content = f.read()
            end_pos = f.tell()
        return new_content, end_pos
    except Exception:
        return "", from_pos

class TaskState:
    def __init__(self):
        self.running = False
        self.process: Optional[subprocess.Popen] = None
        self.logs: deque = deque(maxlen=2000)
        self.task_type = "main_pipeline"
        self.task_name = "主流程"
        self.steps = normalize_steps(MAIN_PIPELINE_STEPS)
        self.current_step = -1
        self.step_statuses = ["waiting"] * len(self.steps)   # waiting / running / done / skipped
        self.finished = False
        self.error = False
        self.stopped = False
        self.llm_log_offset = get_llm_log_size()
        self.subscribers: list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = []
        self.lock = threading.Lock()

    def reset(self, task_type: str, task_name: str, steps: list[dict]):
        self.logs.clear()
        self.task_type = task_type
        self.task_name = task_name
        self.steps = normalize_steps(steps)
        self.current_step = -1
        self.step_statuses = ["waiting"] * len(self.steps)
        self.finished = False
        self.error = False
        self.stopped = False
        self.llm_log_offset = get_llm_log_size()

    def get_steps_payload(self) -> list[dict]:
        return [
            {
                "index": step["index"],
                "name": step["name"],
            }
            for step in self.steps
        ]

    def snapshot(self) -> dict:
        return {
            "task_type": self.task_type,
            "task_name": self.task_name,
            "steps": self.get_steps_payload(),
            "running": self.running,
            "current_step": self.current_step,
            "step_statuses": list(self.step_statuses),
            "statuses": list(self.step_statuses),
            "finished": self.finished,
            "error": self.error,
            "stopped": self.stopped,
            "log_count": len(self.logs),
            "llm_log_offset": self.llm_log_offset,
        }

    def make_event(self, payload: dict) -> dict:
        event = dict(payload)
        event.setdefault("task_type", self.task_type)
        event.setdefault("task_name", self.task_name)
        event.setdefault("steps", self.get_steps_payload())
        event.setdefault("current_step", self.current_step)
        event.setdefault("statuses", list(self.step_statuses))
        event.setdefault("running", self.running)
        event.setdefault("finished", self.finished)
        event.setdefault("error", self.error)
        event.setdefault("stopped", self.stopped)
        event.setdefault("llm_log_offset", self.llm_log_offset)
        return event

    def replace_running_steps(self, next_status: str):
        for i, status in enumerate(self.step_statuses):
            if status == "running":
                self.step_statuses[i] = next_status

    def mark_step(self, idx: int, status: str):
        if idx < 0 or idx >= len(self.step_statuses):
            return
        for i in range(idx):
            if self.step_statuses[i] == "running":
                self.step_statuses[i] = "done"
        self.step_statuses[idx] = status
        self.current_step = idx

    def start_single_step_task(self):
        if self.step_statuses:
            self.current_step = 0
            self.step_statuses[0] = "running"

    def broadcast(self, event: dict):
        """向所有 SSE 订阅者推送事件"""
        def _push(q, e):
            try:
                q.put_nowait(e)
            except asyncio.QueueFull:
                pass

        for q, loop in self.subscribers:
            try:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(_push, q, event)
            except Exception:
                pass

task_state = TaskState()


# ── 工具函数 ─────────────────────────────────────────────

def read_llm_config() -> dict:
    """解析 LLM.txt 配置"""
    config = {"models": [], "url": "", "key": ""}
    if not LLM_CONFIG.exists():
        return config
    for line in LLM_CONFIG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("URL:"):
            config["url"] = line[4:]
        elif line.startswith("KEY:"):
            config["key"] = line[4:]
        elif line.startswith("mods:"):
            config["models"] = [m.strip() for m in line[5:].split(",") if m.strip()]
    return config


def list_games() -> list[str]:
    """扫描 commands/ 目录获取 game 列表"""
    if not COMMANDS_DIR.exists():
        return []
    return [f.stem for f in COMMANDS_DIR.glob("*.json")]


def load_commands(game: str) -> list[dict]:
    """加载 commands JSON"""
    path = COMMANDS_DIR / f"{game}.json"
    if not path.exists():
        raise FileNotFoundError(f"commands/{game}.json 不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("commands", [])


def load_jsonl(filepath: Path, max_lines: int = 0) -> list[dict]:
    """加载 JSONL 文件"""
    results = []
    if not filepath.exists():
        return results
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            results.append(json.loads(line))
            if max_lines and len(results) >= max_lines:
                break
    return results


def get_output_stats(game: str) -> dict:
    """获取 output 目录的统计信息"""
    game_dir = OUTPUT_DIR / game
    if not game_dir.exists():
        return {"commands": [], "total": 0, "labels": {}, "source_types": {}}

    commands_stats = []
    all_samples = []

    # 各 command 子目录
    for cmd_dir in sorted(game_dir.iterdir()):
        if not cmd_dir.is_dir():
            continue
        cmd_id = cmd_dir.name
        stats = {"command_id": cmd_id, "template": 0, "adversarial": 0, "paraphrase": 0, "total": 0}
        for ftype in ["template", "adversarial", "paraphrase"]:
            fpath = cmd_dir / f"{ftype}.jsonl"
            if fpath.exists():
                samples = load_jsonl(fpath)
                stats[ftype] = len(samples)
                # 按文件名赋予 source_type
                for s in samples:
                    s.setdefault("source_type", ftype)
                all_samples.extend(samples)
        stats["total"] = stats["template"] + stats["adversarial"] + stats["paraphrase"]
        commands_stats.append(stats)

    # 全局负样本
    gn_path = game_dir / "global_negatives.jsonl"
    gn_samples = load_jsonl(gn_path)
    for s in gn_samples:
        s.setdefault("source_type", "global_negative")
    all_samples.extend(gn_samples)

    # 统计
    labels = {}
    source_types = {}
    for s in all_samples:
        # 适配两种 label 格式
        label = s.get("label", "unknown")
        if isinstance(label, dict):
            label = label.get("type", "unknown")
        labels[label] = labels.get(label, 0) + 1

        st = s.get("source_type", "unknown")
        source_types[st] = source_types.get(st, 0) + 1

    return {
        "commands": commands_stats,
        "global_negatives": len(gn_samples),
        "total": len(all_samples),
        "labels": labels,
        "source_types": source_types,
    }


def load_json_file(filepath: Path):
    """Load JSON file with a small dashboard-friendly error surface."""
    if not filepath.exists():
        return None
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e), "_path": str(filepath)}


def iso_mtime(filepath: Path) -> str:
    """Return file mtime as ISO string."""
    return datetime.datetime.fromtimestamp(
        filepath.stat().st_mtime,
        tz=datetime.timezone.utc,
    ).astimezone().isoformat(timespec="seconds")


def summarize_audit_round_file(filepath: Path) -> dict:
    """Extract the dashboard-facing summary from one audit round artifact."""
    payload = load_json_file(filepath)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid audit round payload: {filepath}")

    audit_result = payload.get("audit_result", {}) if isinstance(payload.get("audit_result"), dict) else {}
    audit_summary = audit_result.get("audit_summary", {}) if isinstance(audit_result.get("audit_summary"), dict) else {}
    sample_results = audit_result.get("sample_results", []) if isinstance(audit_result.get("sample_results"), list) else []
    systemic_findings = audit_result.get("systemic_findings", []) if isinstance(audit_result.get("systemic_findings"), list) else []
    failed_samples = [
        result.get("sample_index")
        for result in sample_results
        if isinstance(result, dict) and result.get("verdict") in {"fail", "fatal", "borderline"}
    ]

    return {
        "round_index": payload.get("round_index"),
        "file_name": filepath.name,
        "updated_at": iso_mtime(filepath),
        "sample_count_actual": payload.get("sample_count_actual", 0),
        "overall_risk": audit_summary.get("overall_risk", "unknown"),
        "final_verdict": audit_summary.get("final_verdict", "unknown"),
        "total_samples": audit_summary.get("total_samples", 0),
        "pass_count": audit_summary.get("pass_count", 0),
        "borderline_count": audit_summary.get("borderline_count", 0),
        "fail_count": audit_summary.get("fail_count", 0),
        "fatal_count": audit_summary.get("fatal_count", 0),
        "systemic_findings_count": len(systemic_findings),
        "problem_sample_indices": failed_samples,
    }


def get_quality_audit_overview(game: str) -> dict:
    """Read quality audit artifacts for dashboard display."""
    audit_dir = OUTPUT_DIR / game / "quality_audit"
    summary_path = audit_dir / "summary.json"

    if not audit_dir.exists():
        return {
            "exists": False,
            "game": game,
            "output_dir": str(audit_dir),
            "summary": None,
            "derived_summary": None,
            "rounds": [],
        }

    summary_payload = load_json_file(summary_path) if summary_path.exists() else None
    round_paths = sorted(audit_dir.glob("audit_round_*.json"))
    rounds: list[dict] = []
    round_errors: list[dict] = []

    for path in round_paths:
        try:
            rounds.append(summarize_audit_round_file(path))
        except Exception as e:
            round_errors.append({"file_name": path.name, "error": str(e)})

    risk_rank = {"unknown": -1, "low": 0, "medium": 1, "high": 2}
    worst_round = max(rounds, key=lambda item: risk_rank.get(item["overall_risk"], -1), default=None)
    derived_summary = {
        "rounds_found": len(rounds),
        "rounds_completed": len(rounds),
        "fail_count_total": sum(item["fail_count"] for item in rounds),
        "borderline_count_total": sum(item["borderline_count"] for item in rounds),
        "fatal_count_total": sum(item["fatal_count"] for item in rounds),
        "worst_overall_risk": worst_round["overall_risk"] if worst_round else "unknown",
        "latest_updated_at": rounds[-1]["updated_at"] if rounds else None,
    }

    return {
        "exists": bool(summary_path.exists() or round_paths),
        "game": game,
        "output_dir": str(audit_dir),
        "summary": summary_payload,
        "summary_file_updated_at": iso_mtime(summary_path) if summary_path.exists() else None,
        "derived_summary": derived_summary,
        "rounds": rounds,
        "round_errors": round_errors,
    }


def get_quality_audit_round(game: str, round_index: int) -> dict:
    """Read one full audit round artifact."""
    filepath = OUTPUT_DIR / game / "quality_audit" / f"audit_round_{round_index:02d}.json"
    payload = load_json_file(filepath)
    if payload is None:
        raise FileNotFoundError(filepath.name)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid audit round payload: {filepath.name}")
    payload["file_name"] = filepath.name
    payload["updated_at"] = iso_mtime(filepath)
    return payload


# ── 生成任务管理 ──────────────────────────────────────────

class GenerateRequest(BaseModel):
    game: str = "mmorpg"
    model: Optional[str] = None
    command_id: Optional[str] = None
    command_ids: list[str] = Field(default_factory=list)
    think_mode: bool = False
    think_level: str = "high"
    template_count: int = 40
    adversarial_source: int = 10
    paraphrase_source: int = 5
    audit_sample_count: int = 12
    audit_rounds: int = 2
    skip_vocab: bool = False
    skip_aliases: bool = False


class GlobalNegativeRequest(BaseModel):
    game: str = "mmorpg"
    model: Optional[str] = None
    think_mode: bool = False
    think_level: str = "high"
    rounds: int = 3
    dedup_threshold: float = 0.92


class QualityAuditRequest(BaseModel):
    game: str = "mmorpg"
    model: Optional[str] = None
    think_mode: bool = False
    think_level: str = "high"
    sample_count: int = 12
    rounds: int = 2


class FullDataCheckRequest(BaseModel):
    game: str = "mmorpg"
    model: Optional[str] = None
    think_mode: bool = False
    think_level: str = "high"
    batch_size: int = DEFAULT_FULL_CHECK_BATCH_SIZE
    restart: bool = False


class FullCheckIssueReviewRequest(BaseModel):
    game: str = "mmorpg"
    model: Optional[str] = None
    secondary_model: Optional[str] = None
    think_mode: bool = False
    think_level: str = "high"
    restart: bool = False


class FullCheckActionItem(BaseModel):
    sample_id: str
    action: str
    expected_label: Optional[str] = None
    expected_command_id: Optional[str] = None
    expected_slots: Optional[dict[str, str]] = None
    expected_bucket: Optional[str] = None
    resolution_message: Optional[str] = None


class FullCheckActionRequest(BaseModel):
    actions: list[FullCheckActionItem] = Field(default_factory=list)


def get_selected_command_ids(args: GenerateRequest) -> list[str]:
    selected: list[str] = []

    if args.command_id:
        selected.append(args.command_id)
    selected.extend(args.command_ids)

    deduped: list[str] = []
    seen: set[str] = set()
    for command_id in selected:
        if not command_id or command_id in seen:
            continue
        deduped.append(command_id)
        seen.add(command_id)

    return deduped


def build_main_pipeline_command(args: GenerateRequest) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(SCRIPTS_DIR / "generate_training_data.py"),
        "--game", args.game,
        "--template_count", str(args.template_count),
        "--adversarial_source", str(args.adversarial_source),
        "--paraphrase_source", str(args.paraphrase_source),
        "--audit_sample_count", str(args.audit_sample_count),
        "--audit_rounds", str(args.audit_rounds),
        "--skip_global_negatives",
    ]
    if args.model:
        cmd.extend(["--model", args.model])
    for command_id in get_selected_command_ids(args):
        cmd.extend(["--command_id", command_id])
    if args.think_mode:
        cmd.append("--think_mode")
        cmd.extend(["--think_level", args.think_level])
    if args.skip_vocab:
        cmd.append("--skip_vocab")
    if args.skip_aliases:
        cmd.append("--skip_aliases")
    return cmd


def build_global_negative_command(args: GlobalNegativeRequest) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(SCRIPTS_DIR / "generate_global_negatives.py"),
        "--game", args.game,
        "--rounds", str(args.rounds),
        "--dedup_threshold", str(args.dedup_threshold),
    ]
    if args.model:
        cmd.extend(["--model", args.model])
    if args.think_mode:
        cmd.append("--think_mode")
        cmd.extend(["--think_level", args.think_level])
    return cmd


def build_quality_audit_command(args: QualityAuditRequest) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(SCRIPTS_DIR / "audit_training_data.py"),
        "--game", args.game,
        "--sample_count", str(args.sample_count),
        "--rounds", str(args.rounds),
    ]
    if args.model:
        cmd.extend(["--model", args.model])
    if args.think_mode:
        cmd.append("--think_mode")
        cmd.extend(["--think_level", args.think_level])
    return cmd


def build_full_data_check_command(args: FullDataCheckRequest) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(SCRIPTS_DIR / "full_data_check.py"),
        "--game", args.game,
        "--batch_size", str(args.batch_size),
    ]
    if args.model:
        cmd.extend(["--model", args.model])
    if args.restart:
        cmd.append("--restart")
    if args.think_mode:
        cmd.append("--think_mode")
        cmd.extend(["--think_level", args.think_level])
    return cmd


def build_full_check_issue_review_command(args: FullCheckIssueReviewRequest) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(SCRIPTS_DIR / "review_full_check_issues.py"),
        "--game", args.game,
    ]
    if args.model:
        cmd.extend(["--model", args.model])
    if args.secondary_model:
        cmd.extend(["--secondary_model", args.secondary_model])
    if args.restart:
        cmd.append("--restart")
    if args.think_mode:
        cmd.append("--think_mode")
        cmd.extend(["--think_level", args.think_level])
    return cmd


def run_task(
    cmd: list[str],
    *,
    task_type: str,
    task_name: str,
    steps: list[dict],
    auto_start_first_step: bool = False,
):
    """在后台线程中运行 dashboard 任务。"""

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        task_state.reset(task_type, task_name, steps)
        task_state.broadcast(task_state.make_event({"type": "reset"}))
        if auto_start_first_step:
            task_state.start_single_step_task()
            task_state.broadcast(task_state.make_event({
                "type": "step",
                "step": task_state.current_step,
                "status": task_state.step_statuses[task_state.current_step],
            }))

        # CREATE_NEW_PROCESS_GROUP 避免子进程 Ctrl+C 信号影响父进程
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(SCRIPTS_DIR),
            bufsize=1,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        task_state.process = proc

        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n\r")
            task_state.logs.append(line)

            if task_state.stopped:
                task_state.broadcast(task_state.make_event({"type": "log", "line": line}))
                continue

            # 检测步骤切换
            for step in task_state.steps:
                step_key = step.get("key")
                if step_key and step_key in line:
                    idx = step["index"]
                    status = "skipped" if ("跳过" in line or "[SKIP]" in line) else "running"
                    task_state.mark_step(idx, status)
                    task_state.broadcast(task_state.make_event({
                        "type": "step",
                        "step": idx,
                        "status": task_state.step_statuses[idx],
                    }))
                    break

            # 推送日志行
            task_state.broadcast(task_state.make_event({"type": "log", "line": line}))

        proc.wait()

        if task_state.stopped:
            task_state.replace_running_steps("skipped")
            task_state.finished = False
            task_state.error = False
        elif proc.returncode == 0:
            # 标记所有 running 为 done
            task_state.replace_running_steps("done")
            task_state.finished = True
            task_state.broadcast(task_state.make_event({
                "type": "done",
            }))
        else:
            task_state.replace_running_steps("skipped")
            task_state.error = True
            task_state.broadcast(task_state.make_event({
                "type": "error",
                "message": f"进程退出码: {proc.returncode}",
            }))

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[run_task] 异常: {e}\n{tb}", flush=True)
        if task_state.stopped:
            task_state.error = False
        else:
            task_state.replace_running_steps("skipped")
            task_state.error = True
            task_state.broadcast(task_state.make_event({"type": "error", "message": str(e)}))
    finally:
        task_state.running = False
        task_state.process = None


# ── API 路由 ──────────────────────────────────────────────

@app.get("/api/config")
def api_config():
    """获取可用配置（game 列表、模型列表、默认参数）"""
    llm = read_llm_config()
    default_model = llm["models"][-1] if llm["models"] else "deepseek-v3.2"
    default_secondary_model = next((m for m in llm["models"] if m != default_model), None)
    return {
        "games": list_games(),
        "models": llm["models"],
        "defaults": {
            "game": "mmorpg",
            "model": default_model,
            "secondary_model": default_secondary_model,
            "template_count": 40,
            "adversarial_source": 10,
            "paraphrase_source": 5,
            "global_neg_rounds": 3,
            "dedup_threshold": 0.92,
            "audit_sample_count": 12,
            "audit_rounds": 2,
            "full_check_batch_size": DEFAULT_FULL_CHECK_BATCH_SIZE,
        },
    }


@app.get("/api/commands/{game}")
def api_commands(game: str):
    """获取指定 game 的 commands 列表"""
    try:
        cmds = load_commands(game)
        return {"game": game, "commands": cmds}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@app.get("/api/commands/{game}/validate")
def api_validate(game: str):
    """运行校验器并返回结果"""
    cmd_path = COMMANDS_DIR / f"{game}.json"
    if not cmd_path.exists():
        raise HTTPException(404, f"commands/{game}.json 不存在")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "validate_commands.py"), str(cmd_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
            env=env
        )
        return {
            "passed": result.returncode == 0,
            "output": result.stdout,
            "errors": result.stderr,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "校验超时")


@app.post("/api/generate")
def api_generate(req: GenerateRequest):
    """触发数据生成任务"""
    if task_state.running:
        raise HTTPException(409, "已有任务在运行中")

    task_state.running = True

    thread = threading.Thread(
        target=run_task,
        kwargs={
            "cmd": build_main_pipeline_command(req),
            "task_type": "main_pipeline",
            "task_name": "主流程",
            "steps": MAIN_PIPELINE_STEPS,
        },
        daemon=True,
    )
    thread.start()

    return {"status": "started", "message": "生成任务已启动"}


@app.post("/api/global-negatives")
def api_global_negatives_generate(req: GlobalNegativeRequest):
    """触发全局负样本独立任务"""
    if task_state.running:
        raise HTTPException(409, "已有任务在运行中")

    task_state.running = True

    thread = threading.Thread(
        target=run_task,
        kwargs={
            "cmd": build_global_negative_command(req),
            "task_type": "global_negative",
            "task_name": "全局负样本",
            "steps": GLOBAL_NEGATIVE_STEPS,
            "auto_start_first_step": True,
        },
        daemon=True,
    )
    thread.start()

    return {"status": "started", "message": "全局负样本任务已启动"}


@app.post("/api/audit/run")
def api_quality_audit_run(req: QualityAuditRequest):
    """瑙﹀彂璐ㄩ噺鎶芥煡鐙珛浠诲姟"""
    if task_state.running:
        raise HTTPException(409, "宸叉湁浠诲姟鍦ㄨ繍琛屼腑")

    task_state.running = True

    thread = threading.Thread(
        target=run_task,
        kwargs={
            "cmd": build_quality_audit_command(req),
            "task_type": "quality_audit",
            "task_name": "璐ㄩ噺鎶芥煡",
            "steps": QUALITY_AUDIT_STEPS,
            "auto_start_first_step": True,
        },
        daemon=True,
    )
    thread.start()

    return {"status": "started", "message": "璐ㄩ噺鎶芥煡浠诲姟宸插惎鍔?"}


@app.post("/api/full-check/run")
def api_full_data_check_run(req: FullDataCheckRequest):
    """触发全量数据检查任务"""
    if task_state.running:
        raise HTTPException(409, "已有任务在运行中")

    task_state.running = True

    thread = threading.Thread(
        target=run_task,
        kwargs={
            "cmd": build_full_data_check_command(req),
            "task_type": "full_data_check",
            "task_name": "全部数据检查",
            "steps": FULL_DATA_CHECK_STEPS,
        },
        daemon=True,
    )
    thread.start()

    return {"status": "started", "message": "全部数据检查任务已启动"}


@app.post("/api/full-check/review/run")
def api_full_check_issue_review_run(req: FullCheckIssueReviewRequest):
    """触发全部数据检查问题样本复核任务"""
    if task_state.running:
        raise HTTPException(409, "已有任务在运行中")
    llm = read_llm_config()
    available_models = llm["models"]
    primary_model = (req.model or "").strip()
    secondary_model = (req.secondary_model or "").strip()
    if not primary_model:
        raise HTTPException(400, "问题样本复核必须指定主模型")
    if not secondary_model:
        raise HTTPException(400, "问题样本复核必须指定副模型")
    if primary_model == secondary_model:
        raise HTTPException(400, "问题样本复核要求主模型和副模型不同")
    if available_models:
        if primary_model not in available_models:
            raise HTTPException(400, f"主模型不在可用列表中: {primary_model}")
        if secondary_model not in available_models:
            raise HTTPException(400, f"副模型不在可用列表中: {secondary_model}")

    task_state.running = True

    thread = threading.Thread(
        target=run_task,
        kwargs={
            "cmd": build_full_check_issue_review_command(req),
            "task_type": "full_check_issue_review",
            "task_name": "问题样本复核",
            "steps": FULL_CHECK_REVIEW_STEPS,
        },
        daemon=True,
    )
    thread.start()

    return {"status": "started", "message": "问题样本复核任务已启动"}


@app.get("/api/generate/status")
def api_generate_status():
    """获取当前任务状态"""
    return task_state.snapshot()


@app.post("/api/generate/stop")
def api_generate_stop():
    """停止当前任务"""
    proc = task_state.process
    if proc and task_state.running:
        try:
            task_state.stopped = True
            task_state.finished = False
            task_state.error = False
            task_state.replace_running_steps("skipped")
            proc.terminate()
            task_state.broadcast(task_state.make_event({
                "type": "stopped",
                "message": "Stopped",
            }))
            task_state.broadcast(task_state.make_event({"type": "stopped", "message": "任务已被手动停止"}))
            return {"status": "stopped"}
        except Exception as e:
            raise HTTPException(500, str(e))
    else:
        # 进程已经结束（失败或完成），略过并重置状态
        task_state.running = False
        task_state.process = None
        task_state.broadcast(task_state.make_event({"type": "stopped", "message": "任务已结束"}))
        return {"status": "stopped"}


@app.get("/api/output/{game}")
def api_output(game: str):
    """获取输出概览"""
    return get_output_stats(game)


@app.get("/api/audit/{game}")
def api_audit_overview(game: str):
    """Get quality audit overview."""
    return get_quality_audit_overview(game)


@app.get("/api/audit/{game}/rounds/{round_index}")
def api_audit_round(game: str, round_index: int):
    """Get one quality audit round detail."""
    try:
        return get_quality_audit_round(game, round_index)
    except FileNotFoundError:
        raise HTTPException(404, f"audit_round_{round_index:02d}.json 不存在")
    except ValueError as e:
        raise HTTPException(500, str(e))


@app.get("/api/full-check/{game}")
def api_full_check_overview(game: str):
    """Get full-data-check overview."""
    return get_full_check_overview(game)


@app.get("/api/full-check/{game}/issues")
def api_full_check_issues(
    game: str,
    resolution_status: str = "",
    verdict: str = "",
    source_type: str = "",
    q: str = "",
    limit: int = 200,
    offset: int = 0,
):
    """Get full-data-check issues."""
    try:
        return get_full_check_issues(
            game,
            resolution_status=resolution_status,
            verdict=verdict,
            source_type=source_type,
            q=q,
            limit=limit,
            offset=offset,
        )
    except FileNotFoundError:
        raise HTTPException(404, f"full_data_check/{game} 不存在")


@app.post("/api/full-check/{game}/actions/apply")
def api_full_check_apply_actions(game: str, req: FullCheckActionRequest):
    """Apply one or more full-data-check actions."""
    if task_state.running:
        raise HTTPException(409, "任务运行中，暂不能处理检查结果")

    try:
        payloads = []
        for item in req.actions:
            payload = {
                "sample_id": item.sample_id,
                "action": item.action,
            }
            if item.expected_label is not None:
                payload["expected_label"] = item.expected_label
            if item.expected_command_id is not None:
                payload["expected_command_id"] = item.expected_command_id
            if item.expected_slots is not None:
                payload["expected_slots"] = item.expected_slots
            if item.expected_bucket is not None:
                payload["expected_bucket"] = item.expected_bucket
            if item.resolution_message is not None:
                payload["resolution_message"] = item.resolution_message
            payloads.append(payload)
        return apply_full_check_actions(
            game,
            payloads,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/output/{game}/{command_id}/{file_type}")
def api_output_detail(game: str, command_id: str, file_type: str):
    """获取指定 command 的样本数据"""
    allowed = ["template", "adversarial", "paraphrase", "merged", "aliases", "vocab"]
    if file_type not in allowed:
        raise HTTPException(400, f"file_type 只允许: {allowed}")

    if file_type in ("aliases", "vocab"):
        fpath = OUTPUT_DIR / game / command_id / f"{file_type}.json"
        if not fpath.exists():
            raise HTTPException(404, f"文件不存在: {fpath.name}")
        return json.loads(fpath.read_text(encoding="utf-8"))
    else:
        fpath = OUTPUT_DIR / game / command_id / f"{file_type}.jsonl"
        if not fpath.exists():
            raise HTTPException(404, f"文件不存在: {fpath.name}")
        return {"samples": load_jsonl(fpath)}


@app.get("/api/output/{game}/global_negatives")
def api_global_negatives(game: str):
    """获取全局负样本数据"""
    fpath = OUTPUT_DIR / game / "global_negatives.jsonl"
    if not fpath.exists():
        raise HTTPException(404, "global_negatives.jsonl 不存在")
    samples = load_jsonl(fpath)
    # 按 bucket 分组
    buckets = {}
    for s in samples:
        bucket = s.get("meta", {}).get("bucket", "unknown")
        buckets.setdefault(bucket, []).append(s)
    return {"total": len(samples), "buckets": buckets}


@app.get("/api/stream")
async def api_stream():
    """SSE 端点：实时推送生成日志和步骤进度"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    loop = asyncio.get_running_loop()
    subscriber = (queue, loop)
    task_state.subscribers.append(subscriber)

    async def event_generator():
        try:
            llm_log_base = task_state.llm_log_offset
            llm_log_pos = llm_log_base
            last_ping = time.monotonic()
            # 先发送历史日志
            for line in list(task_state.logs):
                yield {"event": "message", "data": json.dumps(task_state.make_event({"type": "log", "line": line}), ensure_ascii=False)}

            # 发送当前步骤状态
            yield {
                "event": "message",
                "data": json.dumps(task_state.make_event({
                    "type": "step",
                    "step": task_state.current_step,
                }), ensure_ascii=False),
            }

            llm_text, llm_log_pos = read_llm_log_chunk(llm_log_pos)
            if llm_text:
                yield {
                    "event": "message",
                    "data": json.dumps(task_state.make_event({
                        "type": "llm_log",
                        "text": llm_text,
                    }), ensure_ascii=False),
                }
                last_ping = time.monotonic()

            while True:
                if llm_log_base != task_state.llm_log_offset:
                    llm_log_base = task_state.llm_log_offset
                    llm_log_pos = llm_log_base
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield {"event": "message", "data": json.dumps(event, ensure_ascii=False)}
                    last_ping = time.monotonic()
                except asyncio.TimeoutError:
                    pass

                llm_text, next_llm_log_pos = read_llm_log_chunk(llm_log_pos)
                if next_llm_log_pos < llm_log_pos:
                    llm_log_pos = llm_log_base
                    llm_text, next_llm_log_pos = read_llm_log_chunk(llm_log_pos)

                if llm_text:
                    llm_log_pos = next_llm_log_pos
                    yield {
                        "event": "message",
                        "data": json.dumps(task_state.make_event({
                            "type": "llm_log",
                            "text": llm_text,
                        }), ensure_ascii=False),
                    }
                    last_ping = time.monotonic()
                elif time.monotonic() - last_ping >= 30.0:
                    yield {"event": "ping", "data": ""}
                    last_ping = time.monotonic()
        except asyncio.CancelledError:
            pass
        finally:
            if subscriber in task_state.subscribers:
                task_state.subscribers.remove(subscriber)

    return EventSourceResponse(event_generator())


# ── LLM 日志 API ───────────────────────────────────────────

@app.get("/api/llm-log-size")
def api_llm_log_size():
    """获取 LLM 日志文件大小"""
    return {"size": get_llm_log_size()}


@app.get("/api/llm-log")
def api_llm_log(from_pos: int = 0):
    """从指定位置读取 LLM 日志新增内容"""
    content, _ = read_llm_log_chunk(from_pos)
    return content


# ── 静态文件 + 首页 ──────────────────────────────────────
STATIC_DIR = DASHBOARD_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ── 启动入口 ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print(f"Dashboard: http://localhost:8765")
    print(f"Project: {PROJECT_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=8765)
