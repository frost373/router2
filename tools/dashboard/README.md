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
| 控制台 | 配置生成参数、触发任务、查看 Pipeline 进度和实时日志、统计图表 |
| 数据浏览 | 按 Command 或类型筛选浏览已生成的样本 |
| Commands | 查看指令注册表、一键校验 |

## 注意事项

- 服务默认监听 **8765** 端口，如需修改可编辑 `tools/dashboard/server.py` 末尾的 `port` 参数
- 生成任务会调用 LLM API，请确保 `LLM.txt` 配置正确且网络可达
- 同一时间只能运行一个生成任务
