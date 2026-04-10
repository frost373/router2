# Dashboard

## 启动方式

```bash
python -m pip install -r tools/dashboard/requirements.txt
python tools/dashboard/server.py
```

默认访问 `http://localhost:8765`。

## 页面结构

| 页面 | 说明 |
| --- | --- |
| 控制台 | 配置公共参数，分别触发“主流程”“质量抽查”“全局负样本”“全部数据检查”“问题样本复核”五个任务入口，查看进度、实时日志和统计图表 |
| 数据浏览 | 浏览 `output/{game}` 下的训练样本与统计结果 |
| Commands | 查看当前 `commands/{game}.json` 内容并执行校验 |

## 控制台任务

- `主流程`：执行训练数据主流水线，产出 `output/{game}/merged_all.jsonl` 与各 command 子目录。
- `质量抽查`：独立读取 `output/{game}/merged_all.jsonl`，只更新 `output/{game}/quality_audit/`。
- `全局负样本`：独立生成并覆盖 `output/{game}/global_negatives.jsonl`。
- `全部数据检查`：对当前全量数据做分批复核，产出 `output/{game}/full_data_check/`，支持继续未完成批次或重新开始。
- `问题样本复核`：逐条复核 `full_data_check` issue，复核工件输出到 `output/{game}/full_data_check/issue_review/`。

## 问题样本复核规则

- 复核任务必须同时指定主模型和副模型，且两者不能相同。
- 两个模型会独立复核同一条 issue；只有两边都能产出可自动执行动作，且结构化动作完全一致时，才允许自动处理。
- `auto_process` 仍只允许三种动作：`apply_expected`、`delete_sample`、`ignore`。
- `pending_confirmation` 不自动改源文件，issue 会继续保留在列表中等待人工处理。
- 双模型的一致自动、共同待确认、结论冲突、模型失败都会在 overview 和 issue 详情中单独展示。
- 勾选“重新开始”时只会清空 `output/{game}/full_data_check/issue_review/`，不会删除 `full_data_check` 第一阶段结果。

## 注意事项

- 服务默认监听 `8765` 端口，如需修改可编辑 `tools/dashboard/server.py` 末尾的 `port` 参数。
- 生成任务会调用 LLM API，请确保 `LLM.txt` 配置正确且网络可达。
- “问题样本复核任务”必须在“全部数据检查”之后执行。
- 如果 `LLM.txt` 只有一个模型，页面会禁用问题样本复核的自动执行入口，因为双模型一致门槛无法满足。
- 同一时间只能运行一个任务。
