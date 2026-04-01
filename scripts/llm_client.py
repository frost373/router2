#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API client. Reads config from LLM.txt and provides plain-text / JSON calls.
"""

import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request

LOG_SEPARATOR = "=" * 80
_CONFIG_CACHE = None
_DEFAULT_MODEL = "deepseek-v3.2"
_DEFAULT_MAX_TOKENS = 8192
_JSON_RETRY_MAX_TOKENS = 32000
_THINKING_MAX_TOKENS = {
    "low": 12000,
    "medium": 20000,
    "high": 32000,
}


def _get_log_path() -> str:
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs",
    )
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "llm_interaction.log")


def start_interaction_log(prompt: str, model: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = open(_get_log_path(), "a", encoding="utf-8")
    log_file.write(f"\n{LOG_SEPARATOR}\n")
    log_file.write(f"[{timestamp}] Model: {model}\n")
    log_file.write(f"--- Prompt ---\n{prompt}\n")
    log_file.write("--- Response ---\n")
    log_file.flush()
    return log_file


def append_interaction_log(log_file, text: str):
    if log_file is None or not text:
        return
    log_file.write(text)
    log_file.flush()


def finish_interaction_log(log_file):
    if log_file is None:
        return
    log_file.write(f"\n{LOG_SEPARATOR}\n")
    log_file.flush()
    log_file.close()


def log_interaction(prompt: str, response: str, model: str):
    log_file = start_interaction_log(prompt, model)
    try:
        append_interaction_log(log_file, response)
    finally:
        finish_interaction_log(log_file)


def _load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "LLM.txt",
    )
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"LLM.txt 不存在: {config_path}")

    url = ""
    key = ""
    models = []

    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("URL:"):
                url = line[4:]
            elif line.startswith("KEY:"):
                key = line[4:]
            elif line.startswith("mods:"):
                models = [m.strip() for m in line[5:].split(",")]

    if not url or not key:
        raise ValueError("LLM.txt 中缺少 URL 或 KEY")

    _CONFIG_CACHE = {"url": url, "key": key, "models": models}
    return _CONFIG_CACHE


def get_available_models() -> list[str]:
    return _load_config()["models"]


def _normalize_think_level(think_level: str | None) -> str:
    if think_level in _THINKING_MAX_TOKENS:
        return think_level
    return "high"


def _resolve_max_tokens(
    max_tokens: int,
    think_mode: bool | None,
    think_level: str | None,
) -> int:
    if think_mode:
        floor = _THINKING_MAX_TOKENS[_normalize_think_level(think_level)]
        return max(max_tokens, floor)
    return max_tokens


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _find_balanced_json_snippet(text: str) -> str:
    start = -1
    for marker in ("{", "["):
        pos = text.find(marker)
        if pos != -1 and (start == -1 or pos < start):
            start = pos

    if start == -1:
        return ""

    stack: list[str] = []
    in_string = False
    escape = False

    for idx in range(start, len(text)):
        ch = text[idx]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                return text[start:idx + 1].strip()
            stack.pop()
            if not stack:
                return text[start:idx + 1].strip()

    return text[start:].strip()


def _is_balanced_json_candidate(text: str) -> bool:
    candidate = text.strip()
    if not candidate or candidate[0] not in "[{":
        return False
    return _find_balanced_json_snippet(candidate) == candidate


def _extract_json_candidate(raw: str) -> str:
    text = _strip_think_blocks(raw.strip())

    for pattern in (
        r"```json\s*(.*?)(?:```|$)",
        r"```\s*(.*?)(?:```|$)",
    ):
        match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            break

    candidate = _find_balanced_json_snippet(text)
    if candidate:
        return candidate
    return text.strip()


def _looks_truncated_json(raw: str, candidate: str) -> bool:
    text = raw.strip()
    if "```" in text and text.count("```") % 2 == 1:
        return True
    if text.count("<think>") != text.count("</think>"):
        return True
    if candidate.strip().startswith(("{", "[")) and not _is_balanced_json_candidate(candidate):
        return True
    return False


def call_llm(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: int = 300,
    think_mode: bool | None = None,
    think_level: str = "high",
    include_reasoning: bool = False,
) -> str:
    config = _load_config()
    model = model or _DEFAULT_MODEL
    max_tokens = _resolve_max_tokens(max_tokens, think_mode, think_level)

    if model not in config["models"]:
        raise ValueError(f"模型 '{model}' 不在可用列表中: {config['models']}")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if think_mode is not None:
        payload["thinking"] = {
            "type": "enabled" if think_mode else "disabled",
        }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['key']}",
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    log_file = start_interaction_log(prompt, model)
    last_error = None

    try:
        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(
                    config["url"],
                    data=data,
                    headers=headers,
                    method="POST",
                )

                reasoning_parts: list[str] = []
                content_parts: list[str] = []
                reasoning_started = False
                reasoning_closed = False

                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line or line.startswith(":"):
                            continue
                        if line == "data: [DONE]":
                            break
                        if not line.startswith("data: "):
                            continue

                        try:
                            chunk = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        reasoning = delta.get("reasoning_content") or ""
                        if reasoning:
                            if not reasoning_started:
                                append_interaction_log(log_file, "<think>\n")
                                reasoning_started = True
                            reasoning_parts.append(reasoning)
                            append_interaction_log(log_file, reasoning)

                        token = delta.get("content") or ""
                        if token:
                            if reasoning_started and not reasoning_closed:
                                append_interaction_log(log_file, "\n</think>\n")
                                reasoning_closed = True
                            content_parts.append(token)
                            append_interaction_log(log_file, token)

                if reasoning_started and not reasoning_closed:
                    append_interaction_log(log_file, "\n</think>\n")

                response_parts: list[str] = []
                if include_reasoning and reasoning_parts:
                    response_parts.append("<think>\n")
                    response_parts.append("".join(reasoning_parts))
                    response_parts.append("\n</think>\n")
                if content_parts:
                    response_parts.append("".join(content_parts))

                full_content = "".join(response_parts)
                if full_content:
                    return full_content
                raise ValueError("API 返回空内容或流解析失败")

            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                ValueError,
                TimeoutError,
            ) as e:
                last_error = e
                if attempt < max_retries:
                    wait = retry_delay * attempt
                    print(f"  [WARN] LLM 调用失败 (尝试 {attempt}/{max_retries}): {e}")
                    print(f"     {wait}s 后重试...")
                    time.sleep(wait)
                else:
                    print(f"  [ERROR] LLM 调用失败，已达最大重试次数: {e}")

        append_interaction_log(log_file, f"\n[ERROR] {last_error}\n")
        raise RuntimeError(f"LLM 调用最终失败: {last_error}")
    finally:
        finish_interaction_log(log_file)


def call_llm_json(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    think_mode: bool | None = None,
    think_level: str = "high",
    **kwargs,
) -> dict | list:
    attempt_tokens = [max_tokens]
    retry_max_tokens = max(
        _JSON_RETRY_MAX_TOKENS if think_mode else 12000,
        _resolve_max_tokens(max_tokens * 2, think_mode, think_level),
    )
    if retry_max_tokens > max_tokens:
        attempt_tokens.append(retry_max_tokens)

    last_error = None
    last_raw = ""

    for attempt_index, attempt_max_tokens in enumerate(attempt_tokens, start=1):
        raw = call_llm(
            prompt,
            model=model,
            temperature=temperature,
            max_tokens=attempt_max_tokens,
            think_mode=think_mode,
            think_level=think_level,
            **kwargs,
        )
        last_raw = raw
        text = _extract_json_candidate(raw)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_error = e
            if attempt_index < len(attempt_tokens) and _looks_truncated_json(raw, text):
                next_tokens = attempt_tokens[attempt_index]
                print(
                    "  [WARN] JSON 解析失败，疑似输出被截断，"
                    f"使用更大的 max_tokens 重试: {attempt_max_tokens} -> {next_tokens}"
                )
                continue
            print(f"  [WARN] JSON 解析失败，原始文本:\n{raw[:500]}")
            raise ValueError(f"LLM 返回无法解析为 JSON: {e}") from e

    raise ValueError(f"LLM 返回无法解析为 JSON: {last_error}\n{last_raw[:500]}")


if __name__ == "__main__":
    print("可用模型:", get_available_models())
    print("\n测试调用...")
    result = call_llm("请用一句话介绍 MMORPG 游戏。", temperature=0.5)
    print(f"回复: {result}")
