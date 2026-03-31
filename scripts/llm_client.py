#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM API 客户端
从 LLM.txt 读取配置，封装 API 调用
"""

import json
import os
import time
import urllib.request
import urllib.error
import re
import datetime

# ── 配置 ──────────────────────────────────────────────────

def log_interaction(prompt: str, response: str, model: str):
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "llm_interaction.log")
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"[{timestamp}] Model: {model}\n")
        f.write(f"--- Prompt ---\n{prompt}\n")
        f.write(f"--- Response ---\n{response}\n")
        f.write(f"{'='*80}\n")

_CONFIG_CACHE = None
_DEFAULT_MODEL = "deepseek-v3.2"


def _load_config() -> dict:
    """从 LLM.txt 读取配置"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LLM.txt"
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
    """获取可用模型列表"""
    return _load_config()["models"]


def call_llm(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: int = 300,
) -> str:
    """
    调用 LLM API

    Args:
        prompt: 用户提示词
        model: 模型名称，默认使用 glm-5
        temperature: 温度参数
        max_tokens: 最大输出 token 数
        max_retries: 最大重试次数
        retry_delay: 重试间隔(秒)
        timeout: 请求超时(秒)

    Returns:
        LLM 输出的文本
    """
    config = _load_config()
    model = model or _DEFAULT_MODEL

    if model not in config["models"]:
        raise ValueError(
            f"模型 '{model}' 不在可用列表中: {config['models']}"
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['key']}",
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                config["url"], data=data, headers=headers, method="POST"
            )
            
            full_content = ""
            full_reasoning = ""
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for line in resp:
                    line = line.decode('utf-8').strip()
                    if not line or line.startswith(":"):
                        continue
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        json_str = line[6:]
                        try:
                            chunk = json.loads(json_str)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                
                                reasoning = delta.get("reasoning_content") or ""
                                if reasoning:
                                    full_reasoning += reasoning
                                    
                                token = delta.get("content") or ""
                                if token:
                                    full_content += token
                        except json.JSONDecodeError:
                            pass
                            
            if full_reasoning:
                full_content = f"<think>\n{full_reasoning}\n</think>\n" + full_content
                
            if full_content:
                log_interaction(prompt, full_content, model)
                return full_content
            else:
                raise ValueError("API 返回空内容或流解析失败")

        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                wait = retry_delay * attempt
                print(f"  [WARN] LLM 调用失败 (尝试 {attempt}/{max_retries}): {e}")
                print(f"     {wait}s 后重试...")
                time.sleep(wait)
            else:
                print(f"  [ERROR] LLM 调用失败，已达最大重试次数: {e}")

    raise RuntimeError(f"LLM 调用最终失败: {last_error}")


def call_llm_json(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    **kwargs,
) -> dict | list:
    """
    调用 LLM 并解析 JSON 返回

    自动处理 Markdown 代码块包裹的 JSON
    """
    raw = call_llm(prompt, model=model, temperature=temperature,
                   max_tokens=max_tokens, **kwargs)

    # 尝试提取 ```json ... ``` 中的内容，兼容推理模型输出的 <think> 标签等前缀文字
    text = raw.strip()
    match = re.search(r"```(?:json)?(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    else:
        # 如果没有代码块，尝试去掉前面的 <think> 块
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON 解析失败，原始文本:\n{raw[:500]}")
        raise ValueError(f"LLM 返回无法解析为 JSON: {e}") from e


# ── 测试入口 ──────────────────────────────────────────────
if __name__ == "__main__":
    print("可用模型:", get_available_models())
    print("\n测试调用...")
    result = call_llm("请用一句话介绍MMORPG游戏。", temperature=0.5)
    print(f"回复: {result}")
