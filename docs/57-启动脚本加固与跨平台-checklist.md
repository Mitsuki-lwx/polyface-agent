# 启动脚本加固与跨平台补齐 — checklist

- 文档类型：checklist（事前三件套之三）
- 版本：v1.0 · 日期：2026-09-19
- 配套：`docs/55-启动脚本加固与跨平台-task.md`、`docs/56-启动脚本加固与跨平台-spec.md`
- 判定方式：每条独立判定；`[x]` = 已实测，`[~]` = 已实现但未实机验证（须在交付说明中标注）

---

## A. 静态校验器

- [x] A1 `scripts/check_scripts.py` 存在，纯标准库，可离线运行
- [x] A2 扫描 `scripts/*.bat` 与 `scripts/*.sh`
- [x] A3 **对现有 4 个脚本（setup/start/stop.bat + start.sh）零 ERROR 零误报**
- [x] A4 输出格式与 `doctor.py` 一致（`[OK]` / `[ERROR]` / `[警告]`）
- [x] A5 无 ERROR 时退出码 0；有 ERROR 时退出码 1
- [x] A6 `--strict` 把 WARN 也当失败
- [x] A7 规则 ID（G*/B*/S*）出现在输出里，便于定位

## B. 校验规则有效性（**每条规则都要有反例测试**）

- [x] B1 G3 检出"续行符后带尾随空白"
- [x] B2 G4 检出硬编码盘符路径（`D:\...`）
- [x] B3 G6 检出未闭合引号
- [x] B4 B1 检出 `.bat` 含裸 LF
- [x] B5 B2/B3/B4 检出缺 `@echo off` / `chcp 65001` / `setlocal`
- [x] B6 B5 检出悬空 `goto :label` / `call :label`
- [x] B7 B6 检出括号不配平
- [x] B8 S1 检出 `.sh` 含 CRLF
- [x] B9 S2 检出缺 shebang
- [x] B10 **变异测试**：故意破坏现有脚本，确认校验器变红；还原后复绿

## C. `.sh` 脚本行为对齐

- [x] C1 `setup.sh` 存在，8 个步骤与 `setup.bat` 逐项对应（spec §3.1）
- [x] C2 `setup.sh` 含 pip 缺失兜底（`ensurepip`）
- [x] C3 `stop.sh` 存在，含"只结束 java/python 系"守卫
- [x] C4 `stop.sh` 结束前确认，取消时不结束任何进程
- [x] C5 `stop.sh` 结束后复查端口释放
- [x] C6 `start.sh` 重写：`java -jar`（不用 Maven）
- [x] C7 `start.sh` 显式 `export POLYFACE_DATA_DIR=<root>/data`
- [x] C8 `start.sh` 轮询 8000 → 8080，非固定 sleep
- [x] C9 `start.sh` 打开 `/`（首页），不是 `/health`
- [x] C10 `start.sh` 先跑 `doctor.py --quiet`，阻塞则中止
- [x] C11 三个 `.sh` 全部 `chmod +x` 且 `bash -n` 语法检查通过

## D. 实机验证（本环境可执行部分）

> 全部在 **Git Bash（Windows 上的 bash）** 完成，夹具是**重新打的发布包**解压到
> 含中文与空格的 `D:\脚本 验证4\`。**真 macOS/Linux 未实测**（见 `docs/58` §7）。
> 因本机 8000 被 `wegame`、5992 被 `WorkBuddyAI.exe` 占用，走 `18000/18080` 覆盖路径。

- [x] D1 `bash -n` 三个脚本均通过（语法层）
- [x] D2 `setup.sh` 在隔离目录实跑成功（建 venv + 装依赖 + 生成 .env）
- [x] D3 `start.sh` 实跑：两个服务就绪，`/health` 返回 `version`
- [x] D4 数据落在**包内** `data/`，包外无残留
- [x] D5 端到端生成一条稿（`qa_passed`）
- [x] D6 `stop.sh` 实跑：端口全部释放
- [x] D7 二次 `start.sh` 可用（幂等），数据仍在

## E. 文档同步

- [x] E1 `docs/25` D9 由 `[ ]` 更新（含"未在真 macOS/Linux 实测"的限定）
- [x] E2 `docs/48` §6 对应行更新（`.bat` 残余风险收窄的说明）
- [x] E3 README 的快速开始补充 macOS/Linux 路径
- [x] E4 交付说明 `docs/58` 产出，§残余项必须写明：
      **本环境是 Git Bash 而非真 Linux/macOS**、`lsof`/`ss` 分支未实测、`.bat` 仍未实机执行
- [x] E5 代码与脚本已提交，工作区干净

---

## 验收结论

| 项 | 结果 | 备注 |
|---|---|---|
| A~E 全部勾选 | ✅ 全部完成 | |
| 校验器零误报 | ✅ 7 个真实脚本 0 错误 / 0 警告 | 过程中 4 次误报全部**改规则**而非改脚本 |
| 校验器自身测试 | ✅ 56 项通过 | 含"对真实脚本零误报"硬守卫 |
| `.sh` 实机验证 | ✅ D1~D7 全部通过 | 隔离发布包 + 中文空格路径 + 关掉 MSYS 转换的最恶劣条件 |
| 回归测试 | ✅ Python 260 / Java 43 | |
| 变异测试 | ✅ 2 次注入均被检出 | 脚本校验器 S8、doctor 端口检测 |
| **未真机验证项** | ⚠️ 见 `docs/58` §7 | **真 macOS/Linux**（`lsof`/`ss`/`open` 分支一行未执行）；
**`.bat` 仍无法执行**（本环境拦截 `cmd.exe`）；**真实 LLM 模式下走 `.sh`**未跑 |
| 顺带修掉 | 3 个真实缺陷 | 端口检测漏报（D1）、端口写死（D2）、POSIX 路径未转换（D3） |
