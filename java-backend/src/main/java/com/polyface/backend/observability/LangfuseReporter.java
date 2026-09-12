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
        body.put("traceId", traceId);
        body.put("name", name);
        if (durationMs > 0) {
            body.put("startTime", Instant.now().minusMillis(durationMs).toString());
            body.put("endTime", ts);
        }
        spanCreate.put("body", body);
        sendBatch(List.of(spanCreate));
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
                        if (resp.statusCode() >= 300) {
                            log.warn("Langfuse 上报失败 status={} body={}", resp.statusCode(),
                                    resp.body().length() > 200 ? resp.body().substring(0, 200) : resp.body());
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
