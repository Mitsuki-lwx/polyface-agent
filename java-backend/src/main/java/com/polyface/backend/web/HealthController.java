package com.polyface.backend.web;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import com.fasterxml.jackson.databind.JsonNode;
import com.polyface.backend.client.PythonClient;

@RestController
public class HealthController {

    private final PythonClient python;

    public HealthController(PythonClient python) {
        this.python = python;
    }

    /**
     * 合并自身状态与 Python LLM 服务状态。
     * 关键：透出 mock 标记，供前端徽章正确显示「离线演示模式 / 真实 LLM」。
     * Python 不可用时视为 mock/离线（本项目 mock 即"无 Key 可跑"的兜底）。
     */
    @GetMapping("/health")
    public Map<String, Object> health() {
        boolean mock = true;
        String model = null;
        try {
            JsonNode py = python.health();
            mock = py.path("mock").asBoolean(true);
            String m = py.path("model").asText("");
            model = m.isEmpty() ? null : m;
        } catch (Exception e) {
            // Python 服务不可达：保持 mock=true，前端提示离线演示模式
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", "ok");
        out.put("service", "polyface-backend");
        out.put("mock", mock);
        if (model != null) {
            out.put("model", model);
        }
        out.put("time", LocalDateTime.now().toString());
        return out;
    }

    @GetMapping("/api/info")
    public Map<String, String> info() {
        return Map.of(
                "name", "Polyface · 同源万面",
                "doc", "docs/ 目录查看方案；POST /api/analyze 转发到 Python LLM 服务");
    }
}
