# Spec Delta

## Purpose

本地 HTTP 服务的**请求来源准入**：决定哪些来自浏览器的请求可以打到本机服务上，
以及被拒时是什么行为。存在的理由是"监听 127.0.0.1"本身**拦不住**任意网页 ——
DNS rebinding 能让任意域名变成"同源"，跨站简单请求则根本不需要预检。
本能力把"谁能打进来"变成一条**显式、可测、可配**的规则。

## ADDED Requirements

### Requirement: 只接受回环 Host

服务 MUST 拒绝 `Host` 不属于回环集合的请求。回环集合 =
`127.0.0.1`、`localhost`、`[::1]`，允许带端口。

被拒时 MUST 返回 `403`，且响应正文 MUST 指出"请求来源不被允许"这一原因
（让人能分辨"被拦"和"服务坏了"）。

#### Scenario: 伪造 Host（DNS rebinding 的落地形态）

- **WHEN** 请求带 `Host: evil.example.com` 打到本机端口（如 `GET /api/materials`）
- **THEN** 返回 `403`，且**不返回任何业务数据**

#### Scenario: 本机访问不受影响

- **WHEN** 请求带 `Host: 127.0.0.1:<port>` 或 `localhost:<port>`
- **THEN** 正常处理（与加闸门之前完全一致）

### Requirement: 跨站 Origin 被拒

带 `Origin` 请求头的请求，其来源 MUST 命中允许集合才放行。
允许集合默认 = 本机回环的任意端口（`http(s)://127.0.0.1:*`、`http(s)://localhost:*`、
`http(s)://[::1]:*`），并 MUST 可通过配置追加（用户把工作台反代到别的域名时需要）。

**不带 `Origin` 的请求 MUST 放行** —— 命令行 `curl`、以及 Java→Python 的服务端调用都不带它，
把"没有 Origin"当成"跨站"会把正常链路一起打死。

#### Scenario: 恶意页面发起跨站请求

- **WHEN** 请求带 `Origin: https://evil.example.com`
- **THEN** 返回 `403`，且**请求不产生副作用**（不触发 LLM 调用、不写库、不上传文件）

#### Scenario: 用户把工作台反代到自定义域名

- **WHEN** 配置里把该域名加入允许集合，再用它作为 `Origin` 请求
- **THEN** 放行

#### Scenario: 命令行与服务间调用

- **WHEN** 请求**不带** `Origin`（`curl`、Java→Python）
- **THEN** 放行，与加闸门之前一致

### Requirement: 拒绝可观测

每次拒绝 MUST 留下一条可查的痕迹（服务日志或事件），含：被拒的 `Host` / `Origin`、
请求方法与路径、以及判定为拒绝的原因。**MUST NOT** 记录请求正文。

#### Scenario: 排查"为什么我的请求被拦了"

- **WHEN** 任一请求被拒
- **THEN** 日志里能找到该次请求的 `Host`/`Origin` 与原因

### Requirement: 两侧口径一致

Java 与 Python 两个服务 MUST 使用**同一套**判定规则与同一组配置项名，
使"从 8080 被拒"与"从 8000 被拒"对用户是同一个概念。

#### Scenario: 同一份配置同时生效

- **WHEN** 设置 `POLYFACE_ALLOWED_ORIGINS=https://my.example.com`
- **THEN** 两个服务都接受该 `Origin`
