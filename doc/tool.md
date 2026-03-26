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
    ├── vocab.json                       # 填槽词库（targets/uses/配对）
    ├── merged_all.jsonl                 # 全量合并数据
    └── {COMMAND_ID}/                    # 如 CAST_ON_TARGET/
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
