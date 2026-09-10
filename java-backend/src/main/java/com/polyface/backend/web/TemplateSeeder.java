package com.polyface.backend.web;

import java.nio.charset.StandardCharsets;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.polyface.backend.store.Store;

/**
 * 预置模板库初始化（FR-62）。
 * 规则：仅当库内不存在 builtin 模板时 seed 一次；此后不覆盖用户任何改动（task Q1 决策）。
 */
@Component
public class TemplateSeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(TemplateSeeder.class);
    private static final String PRESET_PATH = "presets/content-templates.json";

    private final Store store;
    private final ObjectMapper mapper = new ObjectMapper();

    public TemplateSeeder(Store store) {
        this.store = store;
    }

    @Override
    public void run(ApplicationArguments args) {
        try {
            int existing = store.countBuiltinTemplates();
            if (existing > 0) {
                log.info("preset templates already seeded ({} builtin), skip", existing);
                return;
            }
            ClassPathResource res = new ClassPathResource(PRESET_PATH);
            if (!res.exists()) {
                log.warn("preset file not found: {}", PRESET_PATH);
                return;
            }
            String text = new String(res.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            JsonNode root = mapper.readTree(text);
            JsonNode list = root.path("templates");
            int n = 0;
            for (JsonNode t : list) {
                String name = t.path("name").asText("").trim();
                if (name.isEmpty()) {
                    continue;
                }
                store.insertTemplate(
                        "content",
                        name,
                        t.path("voice").asText(""),
                        t.path("opening").asText(""),
                        t.path("structure").toString(),
                        t.path("closing").asText(""),
                        t.path("tag_style").asText(""),
                        t.path("taboo").toString(),
                        true,          // builtin
                        null,
                        1);
                n++;
            }
            log.info("seeded {} preset templates from {}", n, PRESET_PATH);
        } catch (Exception e) {
            // seed 失败不应阻断应用启动
            log.error("template seeding failed: {}", e.getMessage(), e);
        }
    }
}
