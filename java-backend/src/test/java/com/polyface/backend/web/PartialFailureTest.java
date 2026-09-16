package com.polyface.backend.web;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 部分失败保留（FR-42）的 Java 侧验证。
 *
 * <p>用替身 PythonClient 模拟「Python 返回 1 成功 + 1 失败」，
 * 验证：成功稿落库、失败透传、**不整体 502**、计数正确。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-partial",
        "polyface.llm.timeout-sec=240",
        "polyface.llm.fast-timeout-sec=60",
})
class PartialFailureTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @org.springframework.boot.test.mock.mockito.MockBean
    private PythonClient python;

    private final ObjectMapper mapper = new ObjectMapper();
    private long materialId;

    @BeforeEach
    void setUp() throws Exception {
        materialId = store.insertMaterial("素材原文：2023年裸辞。", "长文", "部分失败测试",
                "核心", "理性", "受众", "[{\"type\":\"data\",\"text\":\"2023年裸辞\"}]");

        // analyze 不会被调用（无 confirmed_facts 时 Python 侧自己理解，Java 不调 analyze）
        ObjectNode genResp = mapper.createObjectNode();
        genResp.put("used_mock", true);
        ArrayNode drafts = genResp.putArray("drafts");
        ObjectNode ok = drafts.addObject();
        ok.put("platform_code", "xhs");
        ok.put("platform_name", "小红书");
        ok.set("brief", mapper.createObjectNode());
        ObjectNode payload = mapper.createObjectNode();
        payload.putArray("titles").add("标题");
        payload.put("body", "正文");
        payload.putArray("tags").add("标签");
        ok.set("draft", payload);
        ObjectNode qa = mapper.createObjectNode();
        qa.put("passed", true);
        qa.putArray("issues");
        qa.putArray("warnings");
        ok.set("qa", qa);

        ArrayNode failures = genResp.putArray("failures");
        ObjectNode fail = failures.addObject();
        fail.put("platform", "douyin");
        fail.put("error", "LLMError: 模拟上游失败");

        when(python.postGenerate(any())).thenReturn(genResp);
    }

    @Test
    void partialFailureIsReportedNotFiveHundred() throws Exception {
        mockMvc.perform(post("/api/materials/" + materialId + "/generate")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"platforms\":[\"xhs\",\"douyin\"]}"))
                .andExpect(status().isOk())                  // ← 不是 5xx
                .andExpect(jsonPath("$.ok_count").value(1))
                .andExpect(jsonPath("$.fail_count").value(1))
                .andExpect(jsonPath("$.failures[0].platform").value("douyin"))
                .andExpect(jsonPath("$.failures[0].error").value("LLMError: 模拟上游失败"));
    }

    @Test
    void successfulDraftIsPersisted() throws Exception {
        mockMvc.perform(post("/api/materials/" + materialId + "/generate")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"platforms\":[\"xhs\",\"douyin\"]}"))
                .andExpect(status().isOk());

        var rows = store.draftsByMaterial(materialId);
        org.junit.jupiter.api.Assertions.assertEquals(1, rows.size(), "成功平台应已落库");
        org.junit.jupiter.api.Assertions.assertEquals("xhs", rows.get(0).platformCode());
    }

    @Test
    void failurePlatformIsNotPersisted() throws Exception {
        mockMvc.perform(post("/api/materials/" + materialId + "/generate")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"platforms\":[\"xhs\",\"douyin\"]}"));
        var rows = store.draftsByMaterial(materialId);
        org.junit.jupiter.api.Assertions.assertTrue(
                rows.stream().noneMatch(r -> "douyin".equals(r.platformCode())),
                "失败平台不应落库（也不应产生空稿）");
    }

    @Test
    void allSuccessHasZeroFailCount() throws Exception {
        ObjectNode genResp = mapper.createObjectNode();
        genResp.put("used_mock", true);
        ArrayNode drafts = genResp.putArray("drafts");
        ObjectNode ok = drafts.addObject();
        ok.put("platform_code", "xhs");
        ok.put("platform_name", "小红书");
        ok.set("brief", mapper.createObjectNode());
        ObjectNode payload = mapper.createObjectNode();
        payload.putArray("titles").add("标题");
        payload.put("body", "正文");
        payload.putArray("tags").add("标签");
        ok.set("draft", payload);
        ObjectNode qa = mapper.createObjectNode();
        qa.put("passed", true);
        qa.putArray("issues");
        qa.putArray("warnings");
        ok.set("qa", qa);
        genResp.putArray("failures");
        Mockito.reset(python);
        when(python.postGenerate(any())).thenReturn(genResp);

        mockMvc.perform(post("/api/materials/" + materialId + "/generate")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"platforms\":[\"xhs\"]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok_count").value(1))
                .andExpect(jsonPath("$.fail_count").value(0));
    }
}
