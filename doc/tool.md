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
```

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

### 输出目录

```
output/
└── {game}/                              # 如 mmorpg/
    ├── merged_all.jsonl                 # 全量合并数据
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

基于 FastAPI + 原生 HTML/CSS/JS 的 Web 可视化面板，用于**配置生成参数、触发生成任务、实时查看进度日志、展示数据统计和浏览生成样本**。

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
- **Pipeline 进度条**：7 步骤可视化（词库→扩写→模板→对抗→Paraphrase→全局负样本→合并），实时切换 ⏳/🔄/✅/⏭️ 状态
- **实时日志**：SSE 推送的 Python 脚本输出，自动滚动
- **统计卡片 + 图表**：总样本数、label 分布（环形图）、source_type 分布（条形图）

#### 2）数据浏览器（Tab 切换）

- **Command 卡片列表**：每个 command 的 template/adversarial/paraphrase 样本统计
- **全局负样本卡片**：按 bucket 分组浏览
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
| GET | `/api/generate/status` | 获取当前任务状态 |
| POST | `/api/generate/stop` | 停止当前任务 |
| GET | `/api/output/{game}` | 获取输出统计概览 |
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

