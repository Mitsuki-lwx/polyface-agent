package com.polyface.backend.web;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;

/** 统一异常 → JSON。LLM 服务不可用/超时返回 502。 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    /** Python LLM 服务调用失败的包装异常。 */
    public static class LlmUnavailableException extends RuntimeException {
        public LlmUnavailableException(String message) {
            super(message);
        }
    }

    @ExceptionHandler(LlmUnavailableException.class)
    public ResponseEntity<ObjectNode> handleLlmUnavailable(LlmUnavailableException ex) {
        ObjectNode body = JsonNodeFactory.instance.objectNode();
        body.put("error", "llm_service_unavailable");
        String msg = ex.getMessage() == null ? "unknown" : ex.getMessage().split("\n")[0];
        body.put("detail", msg.replace("\"", "'"));
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(body);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ObjectNode> handleBadRequest(IllegalArgumentException ex) {
        ObjectNode body = JsonNodeFactory.instance.objectNode();
        body.put("error", "bad_request");
        body.put("detail", ex.getMessage() == null ? "bad request" : ex.getMessage());
        return ResponseEntity.badRequest().body(body);
    }

    /**
     * 控制器里抛的 {@link ResponseStatusException}（各种 4xx/5xx）也走**统一错误体**。
     *
     * <p>不处理的话 Spring 会给默认错误体，而它**默认不含 `message`**；前端读的是 `detail`
     * （见 `index.html` 的 `j()`），于是用户只看到一句"请求失败"，
     * 看不到"这个任务是失败，请用 retry 从头重跑"这种**关键指引**。
     */
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<ObjectNode> handleStatus(ResponseStatusException ex) {
        ObjectNode body = JsonNodeFactory.instance.objectNode();
        body.put("error", String.valueOf(ex.getStatusCode().value()));
        body.put("detail", ex.getReason() == null ? "请求失败" : ex.getReason());
        return ResponseEntity.status(ex.getStatusCode()).body(body);
    }
}
