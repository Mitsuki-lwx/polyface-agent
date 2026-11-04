"""端到端真实生成验证（`openspec/changes/platform-differentiation` 任务 5）。

用 2026-11-01 同一篇 1900 字素材（`data/material-1900.txt`），
生成公众号/小红书/知乎三平台成稿，并计算重合率、篇幅比例和格式项。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# 确保能载入 app
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-service"))

from app.config import get_settings
from app.pipeline import diffcheck
from app.pipeline.generate import generate
from app.schemas_gen import GenerateRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("e2e-diff")


def main():
    material_file = ROOT / "data" / "material-1900.txt"
    if not material_file.exists():
        logger.error("素材文件不存在: %s", material_file)
        sys.exit(1)

    raw_text = material_file.read_text(encoding="utf-8")
    settings = get_settings()
    logger.info("开始端到端真实生成测试...")
    logger.info("LLM Mock 模式: %s (需为 False)", settings.llm_mock)
    logger.info("主模型: %s | QA模型: %s", settings.llm_model, settings.llm_qa_model)

    req = GenerateRequest(
        raw_text=raw_text,
        source_kind="长文",
        title="内容复用不是一稿多投",
        platforms=["gzh", "xhs", "zhihu"],
    )

    t0 = time.time()
    structured, drafts, used_mock, failures = generate(req)
    elapsed = time.time() - t0

    logger.info("生成完成，用时: %.1f 秒，used_mock: %s, failures: %d",
                elapsed, used_mock, len(failures))

    if failures:
        for f in failures:
            logger.error("平台 %s 失败: %s", f.platform, f.error)

    drafts_dict = {d.platform_code: d.draft.body for d in drafts}
    tags_dict = {d.platform_code: d.draft.tags for d in drafts}

    diff_eval = diffcheck.evaluate(drafts_dict, tags_dict,
                                   max_overlap=settings.diff_max_overlap,
                                   length_ratio=settings.diff_length_ratio)

    out_file = ROOT / "data" / "e2e-diff-result.json"
    result_data = {
        "elapsed_sec": round(elapsed, 1),
        "used_mock": used_mock,
        "failures": [f.model_dump() for f in failures],
        "diff_eval": diff_eval,
        "drafts": {
            d.platform_code: {
                "titles": d.draft.titles,
                "body": d.draft.body,
                "tags": d.draft.tags,
                "warnings": list(d.qa.warnings),
                "issues": list(d.qa.issues),
                "passed": d.qa.passed,
            }
            for d in drafts
        }
    }
    out_file.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("结果已写入: %s", out_file)

    # 打印复算与对照
    print("\n" + "=" * 60)
    print("【端到端复算结果】")
    print(f"总耗时: {elapsed:.1f}s")
    print(f"两两重合率: {diff_eval['overlap']}")
    print(f"三篇共用句数: {len(diff_eval['shared_by_all'])}")
    if diff_eval["shared_by_all"]:
        print(f"共用句: {diff_eval['shared_by_all']}")
    print(f"正文字数: {diff_eval['length']}")
    print(f"评估问题数: {len(diff_eval['issues'])}")
    for issue in diff_eval["issues"]:
        print(f"  - {issue}")
    print("=" * 60)


if __name__ == "__main__":
    main()
