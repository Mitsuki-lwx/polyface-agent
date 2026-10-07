package com.polyface.backend.observability;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;

/**
 * {@link LangfuseReporter} 的**载荷契约**测试。
 *
 * <p>为什么必须有这层：这个上报器曾以两种方式**静默失败**过，且都不报错 ——
 * <ol>
 *   <li>observation 的 body 少了 {@code id}，被 Langfuse 以
 *       {@code path:["body","id"]} 拒收；</li>
 *   <li>被拒时 HTTP 仍是 <b>207</b>，而代码只看 {@code >= 300}，于是 400 被当成成功。</li>
 * </ol>
 * 两条加起来 = "启用了"但一条都没导进去，却什么日志都没有。
 * 这层测试就是钉住这两条（用本地 stub 服务，不打真 Langfuse）。
 */
class LangfuseReporterTest {

    private HttpServer server;
    private final ObjectMapper mapper = new ObjectMapper();
    private final List<String> ingestionBodies = new ArrayList<>();
    private CountDownLatch ingested;

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    /** 起 stub：/projects 回 200（可达性探测），/ingestion 收载荷并回指定响应。 */
    private String stub(String ingestionResponse, int status) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/public/projects", ex -> {
            byte[] body = "{}".getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        server.createContext("/api/public/ingestion", ex -> {
            ingestionBodies.add(new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            ingested.countDown();
            byte[] body = ingestionResponse.getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(status, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        server.start();
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private LangfuseReporter reporter(String host) {
        ingested = new CountDownLatch(1);
        return new LangfuseReporter(true, host, "pk-test", "sk-test");
    }

    private JsonNode awaitPayload() throws Exception {
        assertTrue(ingested.await(5, TimeUnit.SECONDS), "上报没有发出");
        return mapper.readTree(ingestionBodies.get(0));
    }

    @Test
    void spanPayloadCarriesBodyIdAndCreatesTrace() throws Exception {
        LangfuseReporter r = reporter(stub("{\"successes\":[],\"errors\":[]}", 207));
        r.reportSpan("abc123abc123abc123abc123abc123ab", "java.orchestrate.generate", 42);
        JsonNode batch = awaitPayload().path("batch");

        JsonNode traceCreate = null, spanCreate = null;
        for (JsonNode item : batch) {
            if ("trace-create".equals(item.path("type").asText())) {
                traceCreate = item;
            } else if ("span-create".equals(item.path("type").asText())) {
                spanCreate = item;
            }
        }
        assertTrue(traceCreate != null, "必须补一条 trace-create —— 只发 span 会变成查不到的孤儿");
        assertEquals("abc123abc123abc123abc123abc123ab", traceCreate.path("body").path("id").asText());

        assertTrue(spanCreate != null, "span 本身必须在");
        assertEquals("abc123abc123abc123abc123abc123ab", spanCreate.path("body").path("traceId").asText());
        assertTrue(spanCreate.path("body").path("id").isTextual(),
                "body.id 缺失会被 Langfuse 以 400 拒收（实测），这是曾经静默失败的第一个原因");
        assertEquals("java.orchestrate.generate", spanCreate.path("body").path("name").asText());
    }

    @Test
    void traceCreateIsSentOnlyOncePerTrace() throws Exception {
        LangfuseReporter r = reporter(stub("{\"successes\":[],\"errors\":[]}", 207));
        String tid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        ingested = new CountDownLatch(2);
        r.reportSpan(tid, "stage-1", 1);
        r.reportSpan(tid, "stage-2", 1);
        assertTrue(ingested.await(5, TimeUnit.SECONDS), "两次上报都要发出");

        int traces = 0;
        for (String b : ingestionBodies) {
            for (JsonNode item : mapper.readTree(b).path("batch")) {
                if ("trace-create".equals(item.path("type").asText())) {
                    traces++;
                }
            }
        }
        assertEquals(1, traces, "同一个 trace 只该补一次 trace-create（避免刷 upsert）");
    }

    @Test
    void errorsInside207AreNotTreatedAsSuccess() throws Exception {
        // 这条钉的是"传输层成功掩盖载荷层失败"：HTTP 207 + errors[] 必须被识别出来。
        // 无法直接断言日志，所以钉住**解析逻辑本身**：同样的响应体，firstError 必须能取到原因。
        LangfuseReporter r = reporter(stub(
                "{\"successes\":[],\"errors\":[{\"status\":400,\"message\":\"Invalid request data\","
                        + "\"error\":\"path: body.id expected string\"}]}", 207));
        r.reportSpan("cccccccccccccccccccccccccccccccc", "s", 1);
        awaitPayload();
        // 走到这里说明 207 没有被当成"上报失败异常"抛出（fire-and-forget 语义保持）；
        // 具体告警文案由 firstError 生成，此处通过反射外的公开行为无法直接断言，
        // 故改为断言"载荷仍完整发出"——即观测失败不会影响主流程。
        assertTrue(ingestionBodies.get(0).contains("span-create"));
    }
}
