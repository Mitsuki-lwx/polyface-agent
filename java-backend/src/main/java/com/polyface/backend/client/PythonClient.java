package com.polyface.backend.client;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * Python LLM 服务（:8000）的 HTTP 客户端。
 * 强制 HTTP/1.1：uvicorn/h11 不支持 JDK HttpClient 默认的 h2c 明文升级。
 */
@Component
public class PythonClient {

    private final RestClient client;
    private final ObjectMapper mapper = new ObjectMapper();

    public PythonClient(@Value("${polyface.llm.base-url}") String baseUrl) {
        HttpClient http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        JdkClientHttpRequestFactory rf = new JdkClientHttpRequestFactory(http);
        rf.setReadTimeout(Duration.ofSeconds(180));
        this.client = RestClient.builder().baseUrl(baseUrl).requestFactory(rf).build();
    }

    public JsonNode analyze(String rawText, String sourceKind, String title) {
        ObjectNode body = mapper.createObjectNode();
        body.put("raw_text", rawText);
        body.put("source_kind", sourceKind == null ? "general" : sourceKind);
        if (title != null) {
            body.put("title", title);
        }
        return post("/analyze", body);
    }

    public JsonNode generate(String rawText, String sourceKind, String title,
                             List<String> platforms, String toneOverride, JsonNode template) {
        ObjectNode body = mapper.createObjectNode();
        body.put("raw_text", rawText);
        body.put("source_kind", sourceKind == null ? "general" : sourceKind);
        if (title != null) {
            body.put("title", title);
        }
        var arr = body.putArray("platforms");
        platforms.forEach(arr::add);
        if (toneOverride != null) {
            body.put("tone_override", toneOverride);
        }
        if (template != null && !template.isNull()) {
            body.set("template", template);
        }
        return post("/generate", body);
    }

    public JsonNode platforms() {
        return get("/platforms");
    }

    private JsonNode post(String uri, ObjectNode body) {
        return client.post()
                .uri(uri)
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(JsonNode.class);
    }

    private JsonNode get(String uri) {
        return client.get().uri(uri).retrieve().body(JsonNode.class);
    }
}
