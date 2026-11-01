# Proposal

## Why

Polyface 是**本地 Web 工具**：Java 在 `127.0.0.1:8080`、Python 在 `127.0.0.1:8000`，
**两个服务都没有任何鉴权，也没有校验 `Host` / `Origin`**。

对本机而言"监听 127.0.0.1"**不等于**安全 —— 用户浏览器里打开的任何网页都能打到这两个端口。
2026-10-31 在同一份构建上实测到两条（本机实例 + 假数据，有界验证）：

| # | 复现 | 结果 |
|---|---|---|
| ① **DNS rebinding** | `curl -H "Host: evil.example.com" http://127.0.0.1:8090/api/materials` | **200，返回真实稿件列表** ✗ |
| ② **跨站简单请求** | `curl -H "Origin: https://evil.example.com" -X POST -d '{…}' /api/materials` | **进到了处理器**（502 只是因为 Python 服务挂了，不是被拦）✗ |

①的含义：攻击者只需让 `evil.example.com` 的 DNS 先解析到自己、再改解析到 `127.0.0.1`，
浏览器就会认为**同源** —— CORS 形同虚设，**读得到全部素材与成稿、也能触发付费生成** ✗。
②的含义：不带预检的简单请求（`<form>`、multipart）**会真的执行**，只是读不到响应 ——
足以**消耗用户自己付费的 LLM 额度** ✗。

`docs/checklist_cors.md`（CORS 收紧）已经把"恶意网页能消耗额度"写成了动机，
但**只关掉 CORS 并不解决它**：CORS 管的是"能不能**读**响应"，管不了"请求**会不会被执行**"，
更管不了 DNS rebinding（那时根本就是同源）。

## What Changes

- **新增请求来源守卫**（两侧各一份，行为一致）：
  - `Host` 头必须是 `127.0.0.1` / `localhost` / `[::1]`（可带端口）→ 挡住 DNS rebinding；
  - 带 `Origin` 头的请求，其来源必须在允许集合里（默认：本机回环的任意端口）→ 挡住跨站；
  - 不带 `Origin` 的请求放行（命令行 `curl`、Java→Python 的服务端调用都不带）。
- **允许集合可配**（`POLYFACE_ALLOWED_ORIGINS`，逗号分隔）—— 用户把工作台反代到别的域名时仍能用。
- **拒绝时返回 `403` + 可读原因**（说明是"请求来源不被允许"，不是"服务坏了"）。
- **不引入新依赖、不改端口、不改协议**。

## Capabilities

### New Capabilities

- `local-service-guard`: 本地 HTTP 服务的**请求来源准入** —— 定义"哪些 `Host` / `Origin`
  能打进来"，以及被拒时的可观测行为。两个服务（Java / Python）共用同一套判定口径。

### Modified Capabilities

（无。现有 spec 描述的是**功能行为**，本次只加一道准入闸门，不改任何既有需求。）

## Impact

- **代码**：`java-backend` 新增一个 `Filter`（在 `TraceFilter` 同一层）；
  `python-service` 新增一个 ASGI 中间件（在 `trace_middleware` 同一层）。
- **配置**：新增 `POLYFACE_ALLOWED_ORIGINS`（有默认值，不填即可用）。
- **API**：跨站请求从"能执行"变成"403"；本机页面 / 命令行 / 服务间调用**行为不变**。
- **风险**：若用户的浏览器把工作台当作 `file://` 打开或反代到别的域名，需要显式配置 ——
  文档里写清楚，并在 403 的正文里给出该配置项名字。
