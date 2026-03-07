# ZiWei · 紫微

> 紫微居中，统御群星。  
> Multi-Agent 协同平台 — L0 决策脑 · L1 管理层 · L2 执行 Worker

## 架构概览

```
User Input
    ↓
Orchestrator (TaskRouter)
    ↓
L0 Brain (Opus 4.6) ←─── 看护 L1 · 高可用自重启
    ↓          ↑
L1-A Executor  L1-B Validator  [L1-C Tiebreaker · 仅冲突]
    ↓
L2 Workers (GLM / Qwen / Deepseek / Custom)
    ↓
Infrastructure (Redis · Postgres · Sandbox · Notifier)
```

## 目录结构

```
ziwei/
├── core/               # 核心数据结构、基类
│   ├── models.py       # Task, Manifest, Message, ArtifactVersion
│   ├── enums.py        # RiskLevel, TaskStatus, AgentRole ...
│   └── exceptions.py   # ZiWei 异常体系
├── agents/
│   ├── l0/             # L0 Brain
│   │   ├── brain.py    # 决策脑主逻辑
│   │   ├── planner.py  # 任务规划 & 模型分配
│   │   └── auditor.py  # 最终四维审核
│   ├── l1/             # L1 Manager
│   │   ├── executor.py # L1-A 执行管理者
│   │   ├── validator.py# L1-B 验证管理者
│   │   ├── tiebreaker.py # L1-C 仲裁者
│   │   └── dispatcher.py # 并行任务派发
│   └── l2/             # L2 Worker
│       ├── worker.py   # Worker 运行时 + 权限检查门
│       └── heartbeat.py# 心跳上报
├── infra/
│   ├── storage/        # Redis + Postgres
│   ├── watchdog/       # 健康看护
│   ├── notifier/       # 多渠道推送
│   └── version/        # 版本控制
├── tools/
│   ├── registry.py     # ToolRegistry
│   ├── sandbox.py      # 代码沙箱
│   └── security.py     # Skill/MCP 安全检测
├── api/
│   └── server.py       # FastAPI 入口
└── config/
    └── settings.py     # 全局配置
```

## 快速开始

```bash
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml
# 填入各模型 API Key
python -m ziwei.api.server
```
