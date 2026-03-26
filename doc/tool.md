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
