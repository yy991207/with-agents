"""e2e flow 集成测试 [DEPRECATED]

旧 think → decision → reply 全流程跑通验证
新模型用 多 agent 并发 + 选答替代  整文件需要按新流程重写
"""

import pytest

pytest.skip(
    "think 流程下线后此文件需要重写  暂时跳过",
    allow_module_level=True,
)
