package com.polyface.backend.web;

import java.net.http.HttpClient;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClient;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;

/**
 * 素材解析入口：转发到 Python LLM 服务（:8000 /analyze）。
 *
 * <p>职责边界：Java 只做业务编排与透传，LLM 计算全部在 Python 侧。
 */
@RestController
public class AnalyzeProxyController {

    private static final Logger log = LoggerFactory.getLogger(AnalyzeProxyController.class);

    private final RestClient llmClient;

    public AnalyzeProxyController(@Value("${polyface.llm.base-url}") String llmBaseUrl) {
        // 必须 HTTP/1.1：Python 侧 uvicorn/h11 不支持 JDK HttpClient 默认的 HTTP/2 明文升级(h2c)
        HttpClient http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(java.time.Duration.ofSeconds(5))
                .build();
        JdkClientHttpRequestFactory rf = new JdkClientHttpRequestFactory(http);
        rf.setReadTimeout(java.time.Duration.ofSeconds(120));

        this.llmClient = RestClient.builder()
                .baseUrl(llmBaseUrl)
                .requestFactory(rf)
                .build();
    }

    public record AnalyzeRequest(
            @NotBlank String raw_text,
            String source_kind,
            String title) {
    }

    @PostMapping(value = "/api/analyze", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> analyze(@Valid @RequestBody AnalyzeRequest req) {
        log.info("forward /analyze: source_kind={}, chars={}", req.source_kind(),
                req.raw_text() == null ? 0 : req.raw_text().length());
        try {
            String body = llmClient.post()
                    .uri("/analyze")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(req)
                    .retrieve()
                    .body(String.class);
            return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(body);
        } catch (Exception ex) {
            log.error("python llm service call failed", ex);
            String msg = ex.getMessage() == null ? "unknown" : ex.getMessage().split("\n")[0];
            String errorJson = """
                    {"error": "llm_service_unavailable", "detail": "%s"}
                    """.formatted(msg.replace("\"", "'"));
            return ResponseEntity.status(502).contentType(MediaType.APPLICATION_JSON).body(errorJson);
        }
    }
}
