# multichat-backend

多模型协同对话服务后端。核心模式 think-then-choose:用户提问后,4 个 LLM(DeepSeek/GLM/Kimi/Qwen)各给出 50 字以内"发言理由",用户选择其中一个,被选中的 LLM 借助 deepagents 框架做"深度规划+工具调用"并以 SSE 流式回复。会话与历史使用 MongoDB 持久化。

## 快速开始

推荐使用 conda 环境 `multi-chat`(已预装并验证版本):

```bash
conda activate multi-chat
pip install -e ".[dev]"
```

如果需要纯 venv:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

启动开发服务(应用工厂模式):

```bash
uvicorn multichat.main:create_app --factory --reload --port 8002
```

## 测试

```bash
pytest tests/test_smoke.py
```

## 目录约定

- `src/multichat/` 业务代码
- `tests/` 单元与集成测试
- 配置文件统一从项目根的 `config.yaml` 加载,通过 `multichat.config` 暴露
