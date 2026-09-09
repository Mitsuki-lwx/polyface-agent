package com.polyface.backend.web;

import java.time.LocalDateTime;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {

    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of(
                "status", "ok",
                "service", "polyface-backend",
                "time", LocalDateTime.now().toString());
    }

    @GetMapping("/")
    public Map<String, String> root() {
        return Map.of(
                "name", "Polyface · 同源万面",
                "doc", "docs/ 目录查看方案；POST /api/analyze 转发到 Python LLM 服务");
    }
}
