# Dashboard 可视化面板 — 启动指南

## 环境要求

- Python 3.11+
- 已安装项目依赖

## 首次启动

```bash
# 1. 安装依赖（仅首次需要）
python -m pip install -r tools/dashboard/requirements.txt

# 2. 启动服务
python tools/dashboard/server.py
```

## 日常启动

```bash
python tools/dashboard/server.py
```

启动成功后终端输出：

```
Dashboard: http://localhost:8765
Project: E:\game\router2
INFO:     Uvicorn running on http://0.0.0.0:8765 (Press CTRL+C to quit)
```

浏览器打开 **http://localhost:8765** 即可使用。

## 停止服务

在终端按 `Ctrl+C`。

## 功能概览

| 页面 | 功能 |
|------|------|
| 控制台 | 配置公共参数，分别触发“主流程”“质量抽查”“全局负样本”三个任务入口，查看进度和实时日志、统计图表 |
| 数据浏览 | 按 Command 或类型筛选浏览已生成的样本 |
| Commands | 查看指令注册表、一键校验 |

## 注意事项

- 服务默认监听 **8765** 端口，如需修改可编辑 `tools/dashboard/server.py` 末尾的 `port` 参数
- 生成任务会调用 LLM API，请确保 `LLM.txt` 配置正确且网络可达
- 主流程不会再自动生成全局负样本，只会合并当前已有的 `output/{game}/global_negatives.jsonl`
- 主流程仍会执行质量抽查；“质量抽查任务”会独立读取 `output/{game}/merged_all.jsonl`，只更新 `output/{game}/quality_audit/`
- “全局负样本任务”会单独覆盖 `output/{game}/global_negatives.jsonl`
- 同一时间只能运行一个任务
