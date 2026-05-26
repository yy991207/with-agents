"""TaskManager 单元测试 [DEPRECATED]

本文件原本测试 think-then-choose 流程  多 agent 并发回答重构后
TaskManager 不再有 think / decision / judge 概念  整文件需要按新流程重写
旧实现见 git 历史  跑通新红绿灯后再补单测

参考新红绿灯  scripts/verify_multi_agent_flow.py
"""

import pytest

pytest.skip(
    "think 流程下线后此文件需要按多 agent 并发回答重写  暂时跳过",
    allow_module_level=True,
)
