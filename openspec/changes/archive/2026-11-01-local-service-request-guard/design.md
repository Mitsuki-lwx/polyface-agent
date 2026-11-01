# Design

## Context

动机见 `proposal.md`。这里只记当前状态与约束：

- 两个服务都监听回环，**都没有鉴权**，也**都没校验 `Host`/`Origin`**。
- Java 侧已有一个 `TraceFilter`（`OncePerRequestFilter` 同一层）在做链路标识 ——
  准入判断放在同一层最自然，且能保证"最靠前"。
- Python 侧已有一个 `@app.middleware("http")` 的 `trace_middleware` —— 同理。
- **Java→Python 的服务端调用不带 `Origin`**（`PythonClient` 只设 `Content-Type`/`X-Trace-Id`）——
  这条决定了"没有 Origin 就放行"是硬要求，否则正常链路会被打死。
- 工作台是**同源单页**（`index.html` 由 Java 自己托管），浏览器发出的同源请求
  `Origin` 就是 `http://127.0.0.1:<port>` —— 落在默认允许集合里。

## Goals / Non-Goals

**Goals**

- 让"任意网页驱动本机服务"这件事**失败**（DNS rebinding 与跨站一起挡）。
- 判定规则**两侧同源**（同一套配置项名字与语义）。
- 默认配置下**零感知**：本机页面、命令行、服务间调用行为不变。

**Non-Goals**

- 不做**鉴权**（不引入账号 / token）—— 本地单人工具，加账号是另一种产品。
- 不做 IP 白名单 / 防火墙建议 —— 那是部署层面的事，不是本仓能保证的。
- 不改端口、不改协议、不加依赖。

## Decisions

### D1：用 `Host` + `Origin`，不用 token

- **为什么不用 token**：SPA 是静态托管的，token 要么写进 HTML（等于公开）、
  要么做一次"首次打开时发 token"的引导（改动大、体验割裂）。
- **为什么 `Host` 能挡 rebinding**：rebinding 的攻击面是**浏览器认为同源**，
  但请求到达服务时 `Host` 头是**攻击者的域名** —— 这一条改不了。
- **为什么 `Origin` 能挡跨站**：跨站**简单请求**虽然不预检，但浏览器**一定**带 `Origin`。
- **被否**：只加 CORS —— 管的是"能不能读响应"，管不了"请求会不会执行"，更管不了同源。

### D2：`Host` 白名单是**固定**的，`Origin` 白名单是**可配**的

- `Host` 只需接受回环（改它没有正当理由；用户不会用别的 Host 打本机）。
- `Origin` 需要可配：反代 / 自定义域名是合理需求 → `POLYFACE_ALLOWED_ORIGINS`。
- 默认 `Origin` 集合用**前缀匹配**（`http://127.0.0.1:` / `http://localhost:` / `http://[::1]:`）
  而不是枚举端口 —— 端口是可配的（`POLYFACE_JAVA_PORT`），枚举会漏。

### D3：两侧各写一份，但**共享同一份语义与配置项名**

不抽公共库（两种语言之间抽公共库比重复 20 行更贵）。
两侧测试**用同一组用例**（伪造 Host / 恶意 Origin / 无 Origin / 正常回环），
保证口径不会飘。

### D4：拒绝是 `403` + 可读正文，并**记日志**

正文里带上 `POLYFACE_ALLOWED_ORIGINS` 这个名字 —— 用户被拦时知道去哪儿改。
日志只记 `Host`/`Origin`/方法/路径，**不记正文**（沿用项目既有的脱敏红线）。

## Risks / Trade-offs

- [用户在浏览器里用 `file://` 打开工作台] → 那样根本连不上服务，不涉及本闸门。
- [用户反代到自定义域名却忘了配] → 403 正文与日志都会指出配置项名。
- [IPv6 环境] → `Host` 里带方括号（`[::1]:8080`），实现要按"去掉端口再比"处理，
  不能直接 `split(":")` 取第一段。
- [测试里 `TestClient` 默认 `Host: testserver`] → 现有测试会被闸门挡掉。
  这是**预期**的：测试要么显式带 Host，要么在依赖里关掉闸门（显式关闭，不静默）。
  → 选定：给测试一个"显式关闭"的开关，**默认开着**，避免测试变成假阳性。

## Migration Plan

- 无数据迁移。回滚 = 回退这两个文件。
- 部署后验证：`curl -H "Host: evil.example.com" …` 应得 403；
  正常 `curl` / 工作台 / 生成链路应完全不受影响。

## Open Questions

无（"测试里怎么关"已在 D4 的 Risks 里定掉）。
