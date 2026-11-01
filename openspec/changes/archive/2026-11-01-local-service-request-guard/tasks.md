# Tasks

## 1. Python 侧闸门

- [x] 1.1 `python-service/app/config.py` 加 `allowed_origins: str = ""`（`POLYFACE_ALLOWED_ORIGINS`，逗号分隔，追加到默认回环集合）
- [x] 1.2 新增 `python-service/app/guard.py`：`is_host_allowed(host)` + `is_origin_allowed(origin, extra)` + `judge(method, path, host, origin)` → `(ok, reason)`
- [x] 1.3 `main.py` 加 ASGI 中间件（放在 `trace_middleware` **之前**）：被拒 → `403` + 可读正文 + 一行日志（含 host/origin/方法/路径，不含正文）
- [x] 1.4 提供显式关闭开关 `POLYFACE_GUARD_DISABLED`（默认关＝闸门开着；**只给测试用**，文档注明）

## 2. Java 侧闸门

- [x] 2.1 `application.yml` 加 `polyface.guard.allowed-origins: ${POLYFACE_ALLOWED_ORIGINS:}` 与 `polyface.guard.enabled: ${POLYFACE_GUARD_ENABLED:true}`
- [x] 2.2 新增 `observability/RequestGuardFilter`（`OncePerRequestFilter`，最高优先级）：`Host` 非回环 → 403；`Origin` 不在集合 → 403；无 `Origin` → 放行
- [x] 2.3 403 正文带 `POLYFACE_ALLOWED_ORIGINS` 的名字；日志含 host/origin/方法/路径（不记正文）

## 3. 测试

- [x] 3.1 Python：`tests/test_request_guard.py` —— 伪造 Host → 403 且无数据；恶意 Origin → 403 且**无副作用**；无 Origin → 放行；回环端口 → 放行；`POLYFACE_ALLOWED_ORIGINS` 生效；`[::1]:port` 正确解析
- [x] 3.2 Java：`RequestGuardFilterTest` —— 同一组用例（MockMvc）
- [x] 3.3 既有测试不被误伤：Python 的 API 测试与 Java 的 MockMvc 测试要显式关掉闸门（或带回环 Host），跑全量确认

## 4. 端到端验证（**真起进程**）

- [x] 4.1 起 Java（8090）→ `curl -H "Host: evil.example.com" /api/materials` 得 **403 且无数据**；正常 `curl` 得 200
- [x] 4.2 起 Python（8000）→ `curl -H "Origin: https://evil.example.com" -X POST /generate` 得 **403 且不触发 LLM**（用量文件行数不变）
- [x] 4.3 浏览器打开工作台 → 生成一条成稿**成功**（确认闸门没有误伤同源）

## 5. 文档

- [x] 5.1 `README.zh-CN.md` / `README.md` 的 FAQ 补一条："工作台反代到别的域名被 403？配 `POLYFACE_ALLOWED_ORIGINS`"
- [x] 5.2 `docs/08-ADR` 记 ADR-027：本地服务准入用 Host/Origin，不用 token（含"CORS 挡不住什么"）
- [x] 5.3 归档本 change（`openspec archive`）
