package com.polyface.backend.web;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.fasterxml.jackson.databind.JsonNode;
import com.polyface.backend.client.PythonClient;

/**
 * LLM 用量查询（FR-72）。
 *
 * <p>转发 Python 的 {@code /usage/summary} —— 保持「用量数据由 Python 侧落盘、
 * Java 侧只做转发」的既有分层（Python 不碰数据库）。
 *
 * <p>返回内容**不含 prompt 正文**，只有长度 / token / 耗时 / 重试次数。
 */
@RestController
public class UsageController {

    private final PythonClient python;

    public UsageController(PythonClient python) {
        this.python = python;
    }

    @GetMapping(value = "/api/usage", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode usage(@RequestParam(defaultValue = "50") int limit) {
        return python.usageSummary(limit);
    }
}
