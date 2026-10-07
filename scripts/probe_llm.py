"""最小额度探针：一次极短的 chat 调用，只报状态与耗时，**不打印任何凭据**。

用途：本项目上游额度紧、频繁 429，且**各模型配额独立**。跑真实链路前先探一次，
失败的几秒就返回，不必等整个管线跑几分钟才失败。

用法：
  python scripts/probe_llm.py                                  # 用 .env 里的模型
  python scripts/probe_llm.py "" deepseek-v4-flash,glm-5.2     # 逐个试（换模型常能立刻可用）
  python scripts/probe_llm.py /path/to/.env glm-5.2

退出码：0 = 至少一个模型可用；1 = 全部失败；2 = 配置缺失
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

# 默认取仓库内 python-service/.env（相对脚本位置，不含开发机绝对路径）
ENV = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1]
                  else pathlib.Path(__file__).resolve().parent.parent
                  / "python-service" / ".env")


def load_env() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def main() -> int:
    cfg = load_env()
    base = cfg.get("LLM_BASE_URL", "").rstrip("/")
    key = cfg.get("LLM_API_KEY", "")
    # 第二个参数可覆盖模型（网关各模型配额独立，逐个试很有用）
    models = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else [cfg.get("LLM_MODEL", "")]
    if not (base and key and models[0]):
        print("[失败] .env 缺少 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
        return 2

    print(f"端点 : {base}")
    print(f"Key  : 已配置（{len(key)} 字符，不打印）")
    print()

    ok_any = False
    for model in [m.strip() for m in models if m.strip()]:
        rc = probe_one(base, key, model)
        ok_any = ok_any or rc == 0
    return 0 if ok_any else 1


def probe_one(base: str, key: str, model: str) -> int:
    print(f"--- {model} ---")

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：可用"}],
        # ⚠️ 别设成 8：推理模型（deepseek-v4.1-flash 等）会把 token 全花在 reasoning 上，
        # 正文为空 —— 网关直接回 {"error":"empty response content"}，看着像额度问题，
        # 其实是探针把预算掐太死（2026-10-07 实测踩到）。
        "max_tokens": 64,
        "temperature": 0,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            dt = time.time() - t0
            data = json.loads(raw)
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage") or {}
            print(f"[OK] HTTP {resp.status} · 耗时 {dt:.2f}s")
            print(f"     返回内容 : {content!r}")
            print(f"     token    : {usage}")
            print()
            print("→ 额度可用，可以跑真实链路")
            return 0
    except urllib.error.HTTPError as e:
        dt = time.time() - t0
        detail = e.read().decode("utf-8", "replace")[:400]
        print(f"[失败] HTTP {e.code} · 耗时 {dt:.2f}s")
        print(f"       {detail}")
        if e.code == 429:
            print()
            print("→ 触发限流/额度上限。此刻跑真实链路会浪费等待时间。")
        elif e.code in (401, 403):
            print()
            print("→ 凭据或权限问题（不是额度问题）。")
        return 1
    except Exception as e:  # noqa: BLE001
        dt = time.time() - t0
        print(f"[失败] {type(e).__name__}: {e} · 耗时 {dt:.2f}s")
        return 1


if __name__ == "__main__":
    sys.exit(main())
