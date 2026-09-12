"""pytest 全局配置。

⚠️ **关键**：单元测试必须跑在 mock 模式。
`.env` 可能配置了真实 LLM（`LLM_MOCK=false`），若不强制覆盖，
测试会真的调用外部 API —— 慢、消耗额度、结果不确定、且掩盖 mock 分支的问题。

真实链路验证请用 `pytest -m real tests/test_real_smoke.py`（见该文件说明）。
"""
import os
import tempfile

# 必须在任何 app 模块被导入前设置 —— pytest 会先加载 conftest.py
os.environ["LLM_MOCK"] = "true"
os.environ["LANGFUSE_ENABLED"] = "false"
# 用量写入独立临时文件，避免污染真实统计
os.environ.setdefault(
    "POLYFACE_USAGE_FILE",
    os.path.join(tempfile.gettempdir(), "polyface-test-llm-usage.jsonl"),
)
