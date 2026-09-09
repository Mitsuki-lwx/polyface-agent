package com.polyface.backend.web;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import com.fasterxml.jackson.databind.JsonNode;
import com.polyface.backend.client.PythonClient;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;

/**
 * 素材解析入口：透传到 Python LLM 服务（:8000 /analyze）。
 * Java 只做编排与存储，LLM 计算在 Python 侧。
 */
@RestController
public class AnalyzeProxyController {

    private static final Logger log = LoggerFactory.getLogger(AnalyzeProxyController.class);
    private final PythonClient python;

    public AnalyzeProxyController(PythonClient python) {
        this.python = python;
    }

    public record AnalyzeRequest(
            @NotBlank String raw_text,
            String source_kind,
            String title) {
    }

    @PostMapping(value = "/api/analyze", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> analyze(@Valid @RequestBody AnalyzeRequest req) {
        log.info("/api/analyze: source_kind={}, chars={}", req.source_kind(),
                req.raw_text() == null ? 0 : req.raw_text().length());
        try {
            JsonNode body = python.analyze(req.raw_text(), req.source_kind(), req.title());
            return ResponseEntity.ok(body);
        } catch (Exception ex) {
            log.error("python llm service call failed", ex);
            return ResponseEntity.status(502).body(errorNode(ex));
        }
    }

    static JsonNode errorNode(Exception ex) {
        var node = com.fasterxml.jackson.databind.node.JsonNodeFactory.instance.objectNode();
        String msg = ex.getMessage() == null ? "unknown" : ex.getMessage().split("\n")[0];
        node.put("error", "llm_service_unavailable");
        node.put("detail", msg.replace("\"", "'"));
        return node;
    }
}
