package com.polyface.backend.web;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
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
}
