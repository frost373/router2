#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Embedding API 客户端
从 LLM.txt 读取配置，封装 Embedding API 调用，提供语义去重功能。
"""

import json
import math
import os
import time
import urllib.request
import urllib.error

# ── 配置 ──────────────────────────────────────────────────

_EMBED_CONFIG_CACHE = None
_DEFAULT_EMBED_MODEL = "qwen3-embedding-8b"
_BATCH_SIZE = 32  # 每批最多发送的文本数


def _load_embed_config() -> dict:
    """从 LLM.txt 读取 Embedding 相关配置"""
    global _EMBED_CONFIG_CACHE
    if _EMBED_CONFIG_CACHE is not None:
        return _EMBED_CONFIG_CACHE

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LLM.txt"
    )
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"LLM.txt 不存在: {config_path}")

    url = ""
    key = ""
    model = _DEFAULT_EMBED_MODEL

    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("EMBEDDING_URL:"):
                url = line[len("EMBEDDING_URL:"):]
            elif line.startswith("KEY:"):
                key = line[4:]
            elif line.startswith("embedding_mod:"):
                model = line[len("embedding_mod:"):]

    if not url:
        raise ValueError("LLM.txt 中缺少 EMBEDDING_URL")
    if not key:
        raise ValueError("LLM.txt 中缺少 KEY")

    _EMBED_CONFIG_CACHE = {"url": url, "key": key, "model": model}
    return _EMBED_CONFIG_CACHE


def get_embeddings(
    texts: list[str],
    model: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: int = 120,
) -> list[list[float]]:
    """
    批量获取文本的 embedding 向量。

    自动按 _BATCH_SIZE 分批调用 API。

    Args:
        texts: 待编码文本列表
        model: Embedding 模型名称
        max_retries: 最大重试次数
        retry_delay: 重试间隔(秒)
        timeout: 请求超时(秒)

    Returns:
        与 texts 等长的向量列表
    """
    config = _load_embed_config()
    model = model or config["model"]

    all_embeddings: list[list[float]] = []
    total_batches = math.ceil(len(texts) / _BATCH_SIZE)

    for batch_idx in range(total_batches):
        start = batch_idx * _BATCH_SIZE
        end = min(start + _BATCH_SIZE, len(texts))
        batch_texts = texts[start:end]

        payload = {
            "model": model,
            "input": batch_texts,
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
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                # 按 index 排序确保顺序正确
                emb_data = sorted(result["data"], key=lambda x: x["index"])
                batch_embeddings = [item["embedding"] for item in emb_data]
                all_embeddings.extend(batch_embeddings)
                break

            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
                last_error = e
                if attempt < max_retries:
                    wait = retry_delay * attempt
                    print(f"  ⚠️  Embedding 调用失败 (尝试 {attempt}/{max_retries}): {e}")
                    print(f"     {wait}s 后重试...")
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"Embedding 调用最终失败 (批次 {batch_idx + 1}/{total_batches}): {last_error}"
                    )

    return all_embeddings


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def deduplicate_by_embedding(
    samples: list[dict],
    text_key: str = "input",
    threshold: float = 0.92,
    model: str | None = None,
) -> tuple[list[dict], dict]:
    """
    基于 Embedding 余弦相似度对样本列表进行语义去重。

    对于每个样本，如果它与任何已保留样本的相似度 >= threshold，则丢弃。

    Args:
        samples: 样本列表，每个样本是一个 dict
        text_key: 样本中用于计算 embedding 的文本字段名
        threshold: 去重阈值（余弦相似度）
        model: Embedding 模型名称

    Returns:
        (去重后的样本列表, 统计信息 dict)
    """
    if not samples:
        return [], {"before": 0, "after": 0, "removed": 0}

    texts = [s[text_key] for s in samples]
    print(f"  正在获取 {len(texts)} 条文本的 embedding 向量...")
    embeddings = get_embeddings(texts, model=model)

    kept_indices: list[int] = []
    kept_embeddings: list[list[float]] = []

    for i, emb in enumerate(embeddings):
        is_dup = False
        for kept_emb in kept_embeddings:
            sim = _cosine_similarity(emb, kept_emb)
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept_indices.append(i)
            kept_embeddings.append(emb)

    deduped = [samples[i] for i in kept_indices]
    stats = {
        "before": len(samples),
        "after": len(deduped),
        "removed": len(samples) - len(deduped),
    }
    return deduped, stats


# ── 测试入口 ──────────────────────────────────────────────
if __name__ == "__main__":
    print("Embedding 客户端测试")
    print("=" * 40)

    test_texts = [
        "我去左边拉怪",
        "我过去左边拉一下怪",
        "帮我把boss打断",
        "今天天气真好",
    ]

    print(f"测试文本: {test_texts}")
    embeddings = get_embeddings(test_texts)
    print(f"✅ 成功获取 {len(embeddings)} 个向量")
    print(f"   向量维度: {len(embeddings[0])}")

    print("\n相似度矩阵:")
    for i in range(len(test_texts)):
        for j in range(i + 1, len(test_texts)):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            print(f"  [{i}] vs [{j}]: {sim:.4f}  ({test_texts[i]} ↔ {test_texts[j]})")

    # 测试去重
    print("\n去重测试:")
    test_samples = [{"input": t} for t in test_texts]
    deduped, stats = deduplicate_by_embedding(test_samples, threshold=0.92)
    print(f"  去重前: {stats['before']}  去重后: {stats['after']}  移除: {stats['removed']}")
    print(f"  保留: {[s['input'] for s in deduped]}")
