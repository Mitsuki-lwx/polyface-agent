"""断点续跑的落盘契约（T1）。

设计（见 `docs/spec_resume.md` §5）：**落盘粒度 = 续跑粒度**。

这是本模块存在的全部理由。如果落盘按平台、续跑按素材，就会出现
"存了 3 个平台但续跑时整条重来"—— 存了等于没存。
所以这里只做一件事：**把"某个单位已完成"这件事，以原子、可判读的方式钉在磁盘上**。

三条不变量：

1. **原子写**（临时文件 + 改名）。写一半崩掉不能留下可解析的半成品 ——
   宁可当成"没完成"，也不要读出一个坏产物。
2. **读失败一律当"没完成"**（**不猜**）。半个 JSON 比没有更危险：它会让续跑
   跳过本该重跑的步骤，然后拿着残缺产物当成品。
3. **单位名 = 事件里的阶段名/平台码**。不另造一套名字，就没有"两套名字对不上"的坑。
   代价是中文会进文件名（粗剪的阶段名是中文）—— 各目标平台都支持，可接受。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DONE_DIR = "done"

# 单位名要进文件名，所以必须挡住路径穿越与文件名非法字符。
# 注意：**允许中文**（粗剪阶段名就是中文），只挡真正危险的东西。
_UNSAFE = re.compile(r"[/\\\x00-\x1f]|^\.\.?$")


class UnitNameError(ValueError):
    """单位名不合法。**不静默替换** —— 静默替换会让续跑找不到自己写的东西。"""


def _check(name: str) -> str:
    name = str(name).strip()
    if not name or _UNSAFE.search(name) or len(name) > 64:
        raise UnitNameError(f"非法单位名：{name!r}")
    return name


def done_dir(workdir: str | os.PathLike[str]) -> Path:
    return Path(workdir) / DONE_DIR


def workdir_of(events_path: str | os.PathLike[str] | None) -> Path | None:
    """从事件文件路径反推任务工作目录。

    约定：job 的 workdir 就是 `events.jsonl` 的父目录（Java 侧就是这么建的）。
    没有 events_path（CLI 直跑）时返回 None —— **此时不落盘、也不续跑**，
    保持"命令行单跑"和"任务化"两种用法的行为一致。
    """
    if not events_path:
        return None
    return Path(events_path).parent


def unit_file(workdir: str | os.PathLike[str], kind: str, name: str) -> Path:
    """`kind` 只用于分类与查找（`platform` / `stage`），不参与语义。"""
    return done_dir(workdir) / f"{_check(kind)}-{_check(name)}.json"


def write_unit(workdir: str | os.PathLike[str], kind: str, name: str,
               payload: Any) -> None:
    """原子落盘。**失败要抛** —— 落盘失败必须让上层知道，否则会"以为存了"。"""
    target = unit_file(workdir, kind, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8", newline="\n")
    os.replace(tmp, target)          # 同目录改名：原子


def read_unit(workdir: str | os.PathLike[str], kind: str, name: str) -> Any | None:
    """读一个已完成单位。**任何异常都返回 None**（= 没完成），不向上抛。

    续跑路径上，"读不出来"和"没写过"必须走同一条路 —— 都当成没完成。
    """
    try:
        return json.loads(unit_file(workdir, kind, name).read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return None


def list_units(workdir: str | os.PathLike[str], kind: str) -> list[str]:
    """列出已完成的单位名。损坏/半成品文件**不在其中**（读不出来 = 没完成）。"""
    d = done_dir(workdir)
    prefix = f"{kind}-"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if not f.name.startswith(prefix) or not f.name.endswith(".json"):
            continue
        name = f.name[len(prefix):-len(".json")]
        if read_unit(workdir, kind, name) is not None:
            out.append(name)
    return out


def clear_units(workdir: str | os.PathLike[str], kind: str | None = None) -> int:
    """删掉已完成单位的记录（重跑时用）。返回删除个数。"""
    d = done_dir(workdir)
    if not d.is_dir():
        return 0
    n = 0
    for f in list(d.iterdir()):
        if not f.is_file():
            continue
        if kind is not None and not f.name.startswith(f"{kind}-"):
            continue
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n
