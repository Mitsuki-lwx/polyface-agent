package com.polyface.backend.observability;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Langfuse 上报器（FR-71）—— 让 trace 的**起点**（Java 编排阶段）也进入链路。
 *
 * <p>实现方式：手写 HTTP 调用 Langfuse Public Ingestion API
 * （{@code POST {host}/api/public/ingestion}，Basic base64(publicKey:secretKey)）。
 * 选择手写而非引入官方 SDK：**零新依赖**（纯 JDK HttpClient + 已有 Jackson），
 * 且该端点经实测可用。
 *
 * <p>特性：
 * <ul>
 *   <li><b>fire-and-forget</b>：{@code sendAsync} 异步发送，失败仅告警，绝不阻塞业务</li>
 *   <li><b>可达性探测</b>：启动后首次上报前探一次，服务不可达则整体静默降级</li>
 *   <li><b>trace_id 复用</b>：与 Python 侧使用同一 32 位 W3C trace id，两侧观测归入同一 trace</li>
 * </ul>
 */
@Component
public class LangfuseReporter {

    private static final Logger log = LoggerFactory.getLogger(LangfuseReporter.class);

    private final boolean enabled;
    private final String host;
    private final String publicKey;
    private final String secretKey;

    /**
     * 已经为哪些 traceId 补过 trace-create。**有界**（只留最近 256 个）：
     * 这是个长跑进程，无界集合会一直涨。丢了最坏只是多发一条 upsert，无害。
     */
    private final java.util.Set<String> seenTraces =
            java.util.Collections.newSetFromMap(new java.util.LinkedHashMap<>() {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, Boolean> eldest) {
                    return size() > 256;
                }
            });

    private final HttpClient http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(3))
            .build();
    private final ObjectMapper mapper = new ObjectMapper();

    /** null=未探测；false=不可达（静默降级） */
    private volatile Boolean reachable = null;

    public LangfuseReporter(@Value("${polyface.langfuse.enabled:false}") boolean enabled,
                            @Value("${polyface.langfuse.host:http://localhost:3000}") String host,
                            @Value("${polyface.langfuse.public-key:}") String publicKey,
                            @Value("${polyface.langfuse.secret-key:}") String secretKey) {
        this.enabled = enabled && publicKey != null && !publicKey.isBlank()
                && secretKey != null && !secretKey.isBlank();
        this.host = (host == null ? "" : host).replaceAll("/+$", "");
        this.publicKey = publicKey;
        this.secretKey = secretKey;
        if (this.enabled) {
            log.info("Langfuse 上报已启用：host={}", this.host);
        }
    }

    public boolean isActive() {
        return enabled && isReachable();
    }

    /** 可达性探测（缓存结果；只探一次）。 */
    private boolean isReachable() {
        if (!enabled) {
            return false;
        }
        Boolean cached = reachable;
        if (cached != null) {
            return cached;
        }
        synchronized (this) {
            if (reachable != null) {
                return reachable;
            }
            boolean ok = false;
            try {
                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(host + "/api/public/projects"))
                        .timeout(Duration.ofSeconds(3))
                        .header("Authorization", authHeader())
                        .GET()
                        .build();
                HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
                ok = resp.statusCode() >= 200 && resp.statusCode() < 300;
                if (!ok) {
                    log.warn("Langfuse 不可达（HTTP {}）：上报本次运行降级为关闭", resp.statusCode());
                }
            } catch (Exception e) {
                log.warn("Langfuse 不可达（{}）：上报本次运行降级为关闭", e.getMessage());
            }
            reachable = ok;
            return ok;
        }
    }

    private String authHeader() {
        String raw = publicKey + ":" + secretKey;
        return "Basic " + Base64.getEncoder().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 上报一次完整的 LLM 调用（trace + generation 合并为一批）。
     *
     * @param traceId 与 Python 侧共用的 32 位 trace id
     */
    public void reportLlmCall(String traceId, String name, String model,
                              long durationMs, int promptChars, int completionChars,
                              boolean ok, String attemptInfo) {
        if (!isActive()) {
            return;
        }
        String ts = Instant.now().toString();

        Map<String, Object> traceCreate = new LinkedHashMap<>();
        traceCreate.put("id", UUID.randomUUID().toString());
        traceCreate.put("type", "trace-create");
        traceCreate.put("timestamp", ts);
        Map<String, Object> traceBody = new LinkedHashMap<>();
        traceBody.put("id", traceId);
        traceBody.put("name", name == null ? "polyface-generate" : name);
        traceBody.put("metadata", Map.of(
                "attempt", attemptInfo == null ? "" : attemptInfo,
                "prompt_chars", promptChars,
                "completion_chars", completionChars,
                "source", "java"));
        traceCreate.put("body", traceBody);

        Map<String, Object> genCreate = new LinkedHashMap<>();
        genCreate.put("id", UUID.randomUUID().toString());
        genCreate.put("type", "generation-create");
        genCreate.put("timestamp", ts);
        Map<String, Object> genBody = new LinkedHashMap<>();
        // ⚠️ body 里**必须**带 id：实测当前 Langfuse 会以
        // `path: ["body","id"] expected string` 拒收没有 id 的 observation（HTTP 207 + errors[]）。
        // 这个字段曾经漏了，于是 Java 侧的上报**一直静默失败**（见 sendBatch 的响应检查）。
        genBody.put("id", UUID.randomUUID().toString());
        genBody.put("traceId", traceId);
        genBody.put("name", name == null ? "java-orchestrate" : name);
        genBody.put("model", model == null ? "unknown" : model);
        if (durationMs > 0) {
            genBody.put("startTime", Instant.now().minusMillis(durationMs).toString());
            genBody.put("endTime", ts);
        }
        genCreate.put("body", genBody);

        sendBatch(List.of(traceCreate, genCreate));
    }

    /** 上报一次纯 span（非 LLM 的编排阶段）。 */
    public void reportSpan(String traceId, String name, long durationMs) {
        if (!isActive()) {
            return;
        }
        String ts = Instant.now().toString();
        Map<String, Object> spanCreate = new LinkedHashMap<>();
        spanCreate.put("id", UUID.randomUUID().toString());
        spanCreate.put("type", "span-create");
        spanCreate.put("timestamp", ts);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", UUID.randomUUID().toString());   // 同上：body.id 是必需的
        body.put("traceId", traceId);
        body.put("name", name);
        if (durationMs > 0) {
            body.put("startTime", Instant.now().minusMillis(durationMs).toString());
            body.put("endTime", ts);
        }
        spanCreate.put("body", body);

        // ⚠️ **只发 span 是不够的**：实测 Langfuse 会把"traceId 没有对应 trace"的 observation
        // 收下（observations 里查得到），但 **trace 表里没有这条 trace** ——
        // 于是它在 traces 列表里根本看不见，等于白报。
        // 每个 traceId 首次出现时补一条 trace-create（trace-create 是 upsert，不会重复建）。
        List<Map<String, Object>> batch = new java.util.ArrayList<>();
        if (seenTraces.add(traceId)) {
            Map<String, Object> traceCreate = new LinkedHashMap<>();
            traceCreate.put("id", UUID.randomUUID().toString());
            traceCreate.put("type", "trace-create");
            traceCreate.put("timestamp", ts);
            Map<String, Object> tb = new LinkedHashMap<>();
            tb.put("id", traceId);
            tb.put("name", "polyface");
            traceCreate.put("body", tb);
            batch.add(traceCreate);
        }
        batch.add(spanCreate);
        sendBatch(batch);
    }

    /** 截断日志用的长文本。 */
    private static String truncate(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() > max ? s.substring(0, max) + "…" : s;
    }

    /**
     * 从 ingestion 响应体里取出**第一条**错误（没有错误返回 null）。
     *
     * <p>为什么必须看它：该端点是"部分成功"语义 —— 载荷被拒时 HTTP 仍是 207，
     * 只有 `errors[]` 里写着 400 的原因。
     */
    private String firstError(String body) {
        if (body == null || body.isBlank()) {
            return null;
        }
        try {
            JsonNode errors = mapper.readTree(body).path("errors");
            if (!errors.isArray() || errors.isEmpty()) {
                return null;
            }
            JsonNode e0 = errors.get(0);
            return truncate(e0.path("message").asText("") + " " + e0.path("error").asText(""), 300);
        } catch (Exception e) {
            return "（响应体无法解析：" + truncate(body, 120) + "）";
        }
    }

    private void sendBatch(List<Map<String, Object>> batch) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("batch", batch);
        payload.put("metadata", Map.of());

        String bodyJson;
        try {
            bodyJson = mapper.writeValueAsString(payload);
        } catch (Exception e) {
            log.warn("Langfuse payload 序列化失败：{}", e.getMessage());
            return;
        }

        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(host + "/api/public/ingestion"))
                    .timeout(Duration.ofSeconds(5))
                    .header("Authorization", authHeader())
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(bodyJson, StandardCharsets.UTF_8))
                    .build();
            // fire-and-forget：失败只告警，不阻塞业务
            http.sendAsync(req, HttpResponse.BodyHandlers.ofString())
                    .thenAccept(resp -> {
                        // ⚠️ **不能只看 statusCode**：Langfuse 的 ingestion 是"部分成功"语义 ——
                        // 载荷被拒时返回的是 **207**，错误藏在响应体的 errors[] 里。
                        // 曾经只看 `>= 300`，于是 body.id 缺失导致的 400 被当成成功，
                        // Java 侧的上报**静默失败了不知道多久**（ADR-62 的教训：导出必须有可查询证据）。
                        if (resp.statusCode() >= 300) {
                            log.warn("Langfuse 上报失败 status={} body={}", resp.statusCode(),
                                    truncate(resp.body(), 200));
                            return;
                        }
                        String errs = firstError(resp.body());
                        if (errs != null) {
                            log.warn("Langfuse 上报被拒（HTTP {} 但 errors 非空）：{}",
                                    resp.statusCode(), errs);
                        }
                    })
                    .exceptionally(e -> {
                        log.warn("Langfuse 上报异常：{}", e.getMessage());
                        return null;
                    });
        } catch (Exception e) {
            log.warn("Langfuse 上报异常：{}", e.getMessage());
        }
    }
}
