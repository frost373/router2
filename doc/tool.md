# 工具使用文档

## 1. Commands 校验器

**脚本路径：** `scripts/validate_commands.py`

### 功能说明

根据《指令主表生成提示词》设计文档，对 `commands/*.json` 文件进行合法性校验，并输出统计信息和指令详情。

### 使用方式

```bash
# 校验指定文件
python scripts/validate_commands.py commands/mmorpg.json

# 校验多个文件
python scripts/validate_commands.py commands/mmorpg.json commands/fps.json

# 不传参数时，自动校验 commands/ 目录下所有 .json 文件
python scripts/validate_commands.py
```

### 校验规则

| 类别 | 校验项 | 级别 |
|------|--------|------|
| JSON 格式 | 文件可解析、根对象包含 `commands` 数组 | ❌ 错误 |
| command 数量 | 数量在 15-25 范围内 | ⚠️ 警告 |
| command_id | 全大写字母+下划线格式 | ❌ 错误 |
| command_id | 不使用 `CMD_` 前缀 | ❌ 错误 |
| command_id | 无重复 | ❌ 错误 |
| desc | 非空字符串 | ❌ 错误 |
| slots | 必须是数组 | ❌ 错误 |
| slots | 最多 2 个 slot | ❌ 错误 |
| slots | 每个 slot 包含 `name`、`required`、`type` 字段 | ❌ 错误 |
| slots | name 只允许 `use` 或 `target` | ❌ 错误 |
| slots | type 只允许 `use_span` 或 `target_span` | ❌ 错误 |
| slots | name 与 type 一一对应（`use` ↔ `use_span`，`target` ↔ `target_span`） | ❌ 错误 |
| slots | 不允许出现 `multi` 字段 | ❌ 错误 |
| slots | 双参数时必须是 `use` + `target` 组合 | ❌ 错误 |
| slots | 同一 command 内 slot name 不重复 | ❌ 错误 |
| aliases | 必须是数组 | ❌ 错误 |
| aliases | 数量在 3-6 范围内 | ⚠️ 警告 |
| aliases | 占位符 `{use}` / `{target}` 只使用合法 slot name | ❌ 错误 |
| aliases | 占位符与 slots 定义一致（不能使用未定义的 slot） | ❌ 错误 |
| aliases | 有参数 command 的 aliases 中应使用对应占位符 | ⚠️ 警告 |
| vocab_hints | 如果提供，必须是对象，且只允许 `target_kinds` / `use_kinds` / `pairing_notes` | ❌ 错误 |

### 输出内容

脚本运行后依次输出三部分：

#### 1）校验结果

显示所有错误和警告，最终给出 `✅ 通过` 或 `❌ 失败` 的结论。

#### 2）统计信息

- **无参数 vs 有参数** — 数量及占比
- **单参数 vs 双参数** — 数量及占比（仅统计有参数的 command）
- **参数类型分布** — 仅 target / 仅 use / use+target / 无参数 各多少个

#### 3）指令详情

以文本形式逐条打印每个 command 的：
- `command_id`
- `desc`
- `slots`（格式如 `target:target_span`，无参数显示 `(无参数)`）
- `aliases`（以 `|` 分隔）

### 退出码

- `0` — 所有文件校验通过
- `1` — 存在校验错误

### Commands JSON 格式参考

```json
{
    "commands": [
        {
            "command_id": "ATTACK_TARGET",
            "desc": "命令AI队友立即攻击指定目标",
            "slots": [
                {"name": "target", "required": true, "type": "target_span"}
            ],
            "aliases": [
                "打{target}",
                "攻击{target}",
                "先杀{target}"
            ]
        },
        {
            "command_id": "FOLLOW_ME",
            "desc": "命令AI队友立即跟随玩家行动",
            "slots": [],
            "aliases": [
                "跟我走",
                "跟着我",
                "跟上"
            ]
        }
    ]
}
```

## 2. 训练数据生成器

**主入口：** `scripts/generate_training_data.py`

### 功能说明

基于 `commands/*.json` 中的指令定义，采用"模板主导 + 扰动扩写"策略，自动生成三类训练数据：

| 类型 | 比例 | 说明 |
|------|------|------|
| 程序模板样本 | 60-70% | 别名模板 × 词库填槽 → label=`quick_command` |
| 最小编辑对抗样本 | 20-30% | 从正样本做加条件/加多步/改模糊/改闲聊 → label=`tactical`/`chat` |
| 自由 paraphrase | ~10% | 对正样本自由改写，增加自然度 → label=`quick_command` |

### 生成流程

```
Step 1: 生成填槽词库（targets/uses/配对）
Step 2: LLM 扩写别名模板（每个 command 5→15-20 条）
Step 3: 模板 × 词库填槽 → 正样本
Step 4: 正样本最小编辑 → 对抗样本
Step 5: 正样本自由改写 → paraphrase 样本
Step 6: 合并输出
Step 7: 末尾多轮抽样 → 质量抽查
```

### 背景配置

- 每个游戏可在 `commands/{game}.background.txt` 中提供独立背景描述。
- 词库生成、别名扩写、全局负样本、质量抽查会统一读取该背景文件。
- 如果背景文件不存在，脚本会兼容回退到历史产物或默认背景。

### 使用方式

```bash
# 单 command 测试（推荐先用这个验证）
python scripts/generate_training_data.py --command_id CAST_ON_TARGET

# 全量生成
python scripts/generate_training_data.py --game mmorpg

# 跳过词库和别名扩写（使用已有结果，只重新填槽和生成对抗/paraphrase）
python scripts/generate_training_data.py --skip_vocab --skip_aliases

# 指定模型
python scripts/generate_training_data.py --model kimi-k2.5
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--game` | `mmorpg` | 游戏类型，对应 `commands/` 下的 JSON 文件名 |
| `--command_id` | 无 | 只处理指定 command（用于测试） |
| `--model` | `deepseek-v3.2` | LLM 模型名称 |
| `--skip_vocab` | false | 跳过词库生成，使用已有词库 |
| `--skip_aliases` | false | 跳过别名扩写，使用已有结果 |
| `--template_count` | 40 | 每个 command 生成的模板样本数 |
| `--adversarial_source` | 10 | 每个 command 选多少正样本生成对抗样本 |
| `--paraphrase_source` | 5 | 每个 command 选多少正样本做 paraphrase |
| `--audit_sample_count` | 12 | 单次质量抽查样本数，设为 `0` 可跳过 |
| `--audit_rounds` | 2 | 质量抽查轮数，设为 `0` 可跳过 |

### 输出目录

```
output/
└── {game}/                              # 如 mmorpg/
    ├── merged_all.jsonl                 # 全量合并数据
    ├── quality_audit/                   # 末尾质量抽查结果
    │   ├── audit_round_01.json          # 单轮抽查明细
    │   └── summary.json                 # 抽查汇总
    ├── full_data_check/                 # 全部数据检查结果
    │   ├── manifest.json                # 检查任务信息
    │   ├── summary.json                 # 问题汇总与统计
    │   ├── dataset_snapshot.jsonl       # 检查时使用的数据快照
    │   ├── batch_0001.json              # 单批检查结果
    │   ├── resolution_state.json        # issue 处理状态
    │   ├── actions.jsonl                # 自动/手动处理记录
    │   └── issue_review/                # 问题样本逐条复核结果
    │       ├── manifest.json            # 复核任务信息
    │       ├── reviews.jsonl            # 逐条复核日志
    │       └── summary.json             # 复核汇总
    └── {COMMAND_ID}/                    # 如 CAST_ON_TARGET/
        ├── vocab.json                   # 该指令特定的填槽词库
        ├── aliases.json                 # 扩写后的别名模板
        ├── template.jsonl               # 模板样本
        ├── adversarial.jsonl            # 对抗样本
        ├── paraphrase.jsonl             # paraphrase 样本
        └── merged.jsonl                 # 该 command 合并数据
```

### 输出数据格式（JSONL）

每行一条 JSON：

```json
{
  "text": "快！用冰枪术打炎魔之王！",
  "label": "quick_command",
  "command_id": "CAST_ON_TARGET",
  "slots": {"target": "炎魔之王", "use": "冰枪术"},
  "source_type": "template"
}
```

| 字段 | 说明 |
|------|------|
| `text` | 样本文本 |
| `label` | 标签：`quick_command` / `tactical` / `chat` |
| `command_id` | 对应的指令 ID（对抗样本为空） |
| `slots` | 槽位值（对抗样本为空） |
| `source_type` | 数据来源：`template` / `adversarial` / `paraphrase` |

### 子脚本

各步骤也可独立运行：

```bash
# 仅生成词库
python scripts/generate_vocab.py mmorpg

# 仅扩写别名（可指定 command）
python scripts/expand_aliases.py mmorpg CAST_ON_TARGET

# 仅生成模板样本
python scripts/generate_template_samples.py mmorpg CAST_ON_TARGET

# 仅生成对抗样本
python scripts/generate_adversarial_samples.py mmorpg CAST_ON_TARGET

# 仅生成 paraphrase
python scripts/generate_paraphrase_samples.py mmorpg CAST_ON_TARGET

# 全部数据检查
python scripts/full_data_check.py --game mmorpg

# 逐条复核问题样本，并自动处理可安全落地的问题
python scripts/review_full_check_issues.py --game mmorpg
```

### LLM 配置

通过项目根目录 `LLM.txt` 配置：

```
URL:https://kspmas.ksyun.com/v1/chat/completions
KEY:your-api-key
mods:glm-5,kimi-k2.5,mimo-v2-pro,deepseek-v3.2
```

### 提示词模板

位于 `scripts/prompts/`：

| 文件 | 用途 |
|------|------|
| `vocab_prompt.txt` | 词库生成（targets/uses/配对） |
| `alias_expand_prompt.txt` | 别名模板扩写 |
| `adversarial_prompt.txt` | 最小编辑对抗样本 |
| `paraphrase_prompt.txt` | 自由改写 |
| `quality_audit_prompt.txt` | 末尾质量抽查 |
| `full_check_issue_review_prompt.txt` | 全部数据检查问题样本逐条复核 |

### 词库与命令语义

- `vocab_prompt.txt` 不再绑定固定 MMORPG 类目，而是根据游戏背景、当前 command 文本和语义画像生成词库。
- `targets` 可以覆盖角色、单位、建筑物、地标、设施、交互装置、掉落物、尸体、材料、任务物品、区域短语等，但必须服从当前 command 的语义。
- `uses` 可以覆盖技能、武器、武器模式、消耗品、工具、任务道具等，但只生成当前 command 真正会“使用”的对象。
- 对于仅 `target` 或仅 `use` 的 command，`vocab.json` 中无关数组允许为空。

### 可选字段：vocab_hints

在 `commands/*.json` 的单条 command 中可以添加可选字段 `vocab_hints`，用于增强自动语义推断：

```json
{
  "vocab_hints": {
    "target_kinds": ["building", "interactive_object"],
    "use_kinds": ["tool", "quest_item"],
    "pairing_notes": ["优先生成与终端、门禁、控制台相关的工具组合"]
  }
}
```

- `target_kinds` 可选值：`ally`、`enemy`、`player`、`building`、`landmark`、`facility`、`interactive_object`、`loot`、`corpse`、`area`
- `use_kinds` 可选值：`attack_skill`、`heal_skill`、`buff_skill`、`control_skill`、`consumable`、`tool`、`weapon`、`weapon_mode`、`quest_item`
- `pairing_notes` 为自由文本提示，主要用于约束 `target_use_pairs`

### 全部数据检查与问题样本复核

- `scripts/full_data_check.py` 会对 `output/{game}/merged_all.jsonl` 与源样本做全量检查，输出 `verdict`、`recommended_action` 以及可落地的 `expected_*` 修正建议。
- 全部数据检查支持断点续跑；勾选或传入 `--restart` 时会清空 `output/{game}/full_data_check/` 后重新生成检查工件。
- `scripts/review_full_check_issues.py` 会逐条读取 full-check issue，再次调用 LLM，将结果固定收敛为 `auto_process` 或 `pending_confirmation` 两类。
- `auto_process` 仅允许三种动作：`apply_expected`、`delete_sample`、`ignore`；其中“应保留且无需改动”的 issue 会自动按 `ignore` 关闭。
- 只有高置信且通过本地安全校验的 issue 才会自动执行 `apply_expected`、`delete_sample` 或 `ignore`；不能安全落地的结果会保留为 `pending_confirmation`。
- `issue_review/` 只记录第二阶段复核结论，不覆盖原始 `batch_*.json`；页面展示时会合并两阶段结果。

## 3. Commands 压缩器

**脚本路径：** `scripts/command_registry_compact.py`

### 功能说明

将 `commands/*.json` 中的指令注册表压缩为简洁的文本格式，用于将完整指令列表注入到 LLM 提示词中，减少 token 消耗。

### 压缩格式

每行一条指令，字段以 `|` 分隔：

```
{command_id}|{精简描述}|{alias1}/{alias2}/{alias3}/...
```

- **command_id** — 原始指令 ID
- **精简描述** — 去除"命令AI队友"等前缀后的核心描述
- **aliases** — 所有别名以 `/` 分隔

### 使用方式

```bash
# 压缩指定文件（输出到终端）
python scripts/command_registry_compact.py commands/mmorpg.json

# 输出到文件
python scripts/command_registry_compact.py commands/mmorpg.json -o output/mmorpg_compact.txt

# 不传参数时，自动压缩 commands/ 目录下所有 .json 文件
python scripts/command_registry_compact.py
```

### 作为模块导入

```python
from command_registry_compact import compact_file, compact_registry, load_commands

# 方式1：直接从文件压缩
text = compact_file("commands/mmorpg.json")

# 方式2：先加载再压缩
commands = load_commands("commands/mmorpg.json")
text = compact_registry(commands)
```

### 输出示例

```
ATTACK_TARGET|立即攻击指定目标|打{target}/攻击{target}/先杀{target}/集火{target}/给我揍{target}
FOLLOW_ME|立即跟随玩家行动|跟我走/跟着我/跟上/别掉队/贴我走
HOLD_POSITION|停留在当前位置并保持驻守|原地别动/守在这里/站这别跑/就地守着/留在这
CAST_ON_TARGET|使用指定技能或物品作用于指定目标|对{target}用{use}/给{target}放{use}/拿{use}打{target}/把{use}给{target}/用{use}处理{target}
```

## 4. 全局负样本生成器

**脚本路径：** `scripts/generate_global_negatives.py`

### 功能说明

基于完整 command registry（压缩格式），使用 LLM 批量生成全局级 tactical/chat 负样本。生成后使用 `qwen3-embedding-8b` 进行语义去重。

负样本覆盖 10 个 bucket：

| 分类 | Bucket | 说明 |
|------|--------|------|
| tactical | tactical_self_action | 玩家描述自己的动作 |
| tactical | tactical_team_plan | 团队战术沟通/分工 |
| tactical | tactical_conditional | 包含条件/时机/判断 |
| tactical | tactical_multi_step | 两步及以上组合动作 |
| tactical | tactical_missing_slot | 缺少必要参数信息 |
| tactical | tactical_out_of_registry_teammate_action | 命令AI做不在registry中的动作 |
| tactical | tactical_ambiguous | 模糊/黑话/间接表达 |
| chat | chat_emotion | 纯情绪/吐槽/夸奖 |
| chat | chat_noise | 无意义噪声/符号 |
| chat | chat_out_of_game | 游戏外请求 |

### 使用方式

```bash
# 默认生成（3轮，去重阈值0.92）
python scripts/generate_global_negatives.py --game mmorpg

# 指定轮数和阈值
python scripts/generate_global_negatives.py --game mmorpg --rounds 5 --dedup_threshold 0.90

# 指定模型
python scripts/generate_global_negatives.py --game mmorpg --model kimi-k2.5
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--game` | `mmorpg` | 游戏类型 |
| `--model` | `deepseek-v3.2` | LLM 模型名称 |
| `--rounds` | `3` | 生成轮数（每轮 ~56 条） |
| `--dedup_threshold` | `0.92` | 语义去重余弦相似度阈值 |

### 输出

```
output/{game}/global_negatives.jsonl
```

每行格式：
```json
{
  "input": "我去左边拉怪",
  "label": {"type": "tactical"},
  "meta": {"bucket": "tactical_self_action"},
  "source_type": "global_negative"
}
```

### 流程说明

1. 使用 Commands 压缩器将 `commands/{game}.json` 压缩为 compact 格式
2. 将 compact commands 注入到提示词模板 `global_negative_prompt.txt`
3. 分轮调用 LLM 生成负样本
4. 调用 `qwen3-embedding-8b` 获取所有样本的 embedding 向量
5. 基于余弦相似度进行语义去重
6. 输出去重后的 JSONL 文件

## 5. Embedding 客户端

**脚本路径：** `scripts/embedding_client.py`

### 功能说明

封装 Embedding API 调用（从 `LLM.txt` 读取配置），提供批量向量获取和基于余弦相似度的语义去重功能。

### LLM.txt 配置

需要在 `LLM.txt` 中追加：

```
EMBEDDING_URL:https://kspmas.ksyun.com/v1/embeddings
embedding_mod:qwen3-embedding-8b
```

### 作为模块导入

```python
from embedding_client import get_embeddings, deduplicate_by_embedding

# 获取 embedding 向量
vectors = get_embeddings(["你好", "世界"])

# 语义去重
samples = [{"input": "我去左边"}, {"input": "我过去左边"}]
deduped, stats = deduplicate_by_embedding(samples, text_key="input", threshold=0.92)
print(f"去重: {stats['before']} → {stats['after']}")
```

### 独立测试

```bash
python scripts/embedding_client.py
```

## 6. Dashboard 可视化面板

**目录路径：** `tools/dashboard/`

### 功能说明

基于 FastAPI + 原生 HTML/CSS/JS 的 Web 可视化面板，用于**配置生成参数、触发生成任务、实时查看进度日志、展示数据统计、浏览生成样本，以及查看全部数据检查/问题样本复核结果**。

### 启动方式

```bash
# 安装依赖（首次）
python -m pip install -r tools/dashboard/requirements.txt

# 启动服务
python tools/dashboard/server.py
```

浏览器打开 `http://localhost:8765`

### 功能模块

#### 1）控制台页面

- **配置面板（左侧）**：游戏类型、LLM 模型、指定 Command、各类样本数量、跳过选项等
- **任务卡片**：支持“主流程”“质量抽查”“全局负样本”“全部数据检查”“问题样本复核”五类任务入口；后两者支持继续执行或重新开始
- **Pipeline 进度条**：7 步骤可视化（词库→扩写→模板→对抗→Paraphrase→全局负样本→合并），实时切换 ⏳/🔄/✅/⏭️ 状态
- **实时日志**：SSE 推送的 Python 脚本输出，自动滚动
- **统计卡片 + 图表**：总样本数、label 分布（环形图）、source_type 分布（条形图）

#### 2）数据浏览器（Tab 切换）

- **Command 卡片列表**：每个 command 的 template/adversarial/paraphrase 样本统计
- **全局负样本卡片**：按 bucket 分组浏览
- **全部数据检查面板**：查看检查摘要、issue 列表、推荐动作、复核结论与处理状态
- **点击展开样本表格**：带 label 标签色彩编码和 slots 展示
- **按类型筛选**：All / Template / Adversarial / Paraphrase / Global Neg

#### 3）Commands 查看页面

- 卡片式展示指令注册表（ID、描述、Slots、Aliases）
- 一键校验按钮（调用 `validate_commands.py`）

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取 game 列表、模型列表、默认参数 |
| GET | `/api/commands/{game}` | 获取 commands 列表 |
| GET | `/api/commands/{game}/validate` | 运行校验并返回结果 |
| POST | `/api/generate` | 触发数据生成任务 |
| POST | `/api/global-negatives` | 独立触发全局负样本任务 |
| POST | `/api/audit/run` | 触发质量抽查任务 |
| POST | `/api/full-check/run` | 触发全部数据检查任务 |
| POST | `/api/full-check/review/run` | 触发问题样本逐条复核任务 |
| GET | `/api/generate/status` | 获取当前任务状态 |
| POST | `/api/generate/stop` | 停止当前任务 |
| GET | `/api/output/{game}` | 获取输出统计概览 |
| GET | `/api/audit/{game}` | 获取质量抽查汇总 |
| GET | `/api/audit/{game}/rounds/{round_index}` | 获取单轮质量抽查明细 |
| GET | `/api/full-check/{game}` | 获取全部数据检查汇总（含 review_summary） |
| GET | `/api/full-check/{game}/issues` | 获取全部数据检查 issue 列表（含复核状态） |
| POST | `/api/full-check/{game}/actions/apply` | 批量执行 issue 处理动作 |
| GET | `/api/output/{game}/{command_id}/{file_type}` | 获取样本数据 |
| GET | `/api/output/{game}/global_negatives` | 获取全局负样本 |
| GET | `/api/stream` | SSE 实时推送日志 |

### 文件结构

```
tools/dashboard/
├── server.py          # FastAPI 后端
├── requirements.txt   # Python 依赖
└── static/
    ├── index.html     # 前端页面
    ├── style.css      # 深色主题样式
    └── app.js         # 前端逻辑
```

### 问题样本复核双模型一致机制

- `scripts/review_full_check_issues.py` 新增参数：`--secondary_model`，用于指定问题样本复核的副模型。
- 问题样本复核现在要求主模型和副模型都独立完成同一条 issue 的复核，且结构化自动动作完全一致，才允许自动落地。
- 双模型一致性只比较结构化动作：`action`、`expected_label`、`expected_command_id`、`expected_slots`、`expected_bucket`；不要求自然语言 `reason` 完全一致。
- `issue_review/manifest.json` 会额外记录 `primary_model`、`secondary_model` 和 `consensus_mode`；旧版单模型工件不能直接续跑，需重新开始。
- `issue_review/reviews.jsonl` 会同时保存最终共识结果以及 `primary_review`、`secondary_review` 两份模型明细。
- `GET /api/full-check/{game}` 的 `review_summary` 现在会补充 `dual_model_agree_auto`、`dual_model_agree_pending`、`dual_model_disagree`、`dual_model_errors` 四类统计。
- `GET /api/full-check/{game}/issues` 现在会返回 `review_agreement_status`、`review_disagreement_fields`、`review_primary_result`、`review_secondary_result` 等字段，供 Dashboard 详情页展示双模型结论。
