package com.polyface.backend.web;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import com.polyface.backend.store.Store;

/**
 * 事实确认与稿件编辑闭环（FR-34 / FR-42）的**本地**接口测试。
 *
 * <p>只覆盖不经过 Python 的分支（事实保存/校验、稿件编辑、导出、读单稿）；
 * 「生成时透传 confirmed_facts」涉及跨服务调用，由端到端脚本断言。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "polyface.data-dir=target/test-data-facts")
class FactsAndEditClosureTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    private long materialId;
    private long draftId;

    @BeforeEach
    void setUp() {
        materialId = store.insertMaterial("素材原文：2023年裸辞，写作月入0到3万。", "长文", "测试素材",
                "裸辞做自由职业", "理性干货", "自由职业者",
                "[{\"type\":\"data\",\"text\":\"2023年裸辞，写作月入从0到3万\"}]");
        draftId = store.insertDraft(materialId, "xhs", "小红书",
                "{\"angle\":\"x\"}",
                "{\"titles\":[\"原标题\"],\"body\":\"原正文\",\"tags\":[\"标签A\"]}",
                "{\"passed\":true,\"issues\":[],\"warnings\":[]}", "qa_passed", null, null);
    }

    // ---------------- 事实确认 ----------------

    @Test
    void saveFactsWithConfirmMarksConfirmed() throws Exception {
        mockMvc.perform(put("/api/materials/" + materialId + "/facts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"core_message\":\"新核心\",\"facts\":[{\"type\":\"data\",\"text\":\"事实A\"}],\"confirm\":true}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.facts_confirmed").value(true))
                .andExpect(jsonPath("$.facts_count").value(1));

        Store.MaterialRow m = store.getMaterial(materialId).orElseThrow();
        assertTrue(m.factsConfirmed(), "应标记为已确认");
        assertNotNull(m.factsConfirmedAt(), "应记录确认时间");
        assertEquals("新核心", m.coreMessage(), "核心观点应被更新");
    }

    @Test
    void saveFactsWithoutConfirmKeepsUnconfirmed() throws Exception {
        mockMvc.perform(put("/api/materials/" + materialId + "/facts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"facts\":[{\"type\":\"data\",\"text\":\"事实A\"}],\"confirm\":false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.facts_confirmed").value(false));
        assertTrue(!store.getMaterial(materialId).orElseThrow().factsConfirmed());
    }

    @Test
    void saveFactsWithBlankTextRejected() throws Exception {
        mockMvc.perform(put("/api/materials/" + materialId + "/facts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"facts\":[{\"type\":\"data\",\"text\":\"   \"}],\"confirm\":true}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void saveFactsForMissingMaterialReturns404() throws Exception {
        mockMvc.perform(put("/api/materials/999999/facts")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"facts\":[],\"confirm\":true}"))
                .andExpect(status().isNotFound());
    }

    // ---------------- 稿件编辑 ----------------

    @Test
    void editDraftUpdatesBodyAndStampsEditedAt() throws Exception {
        mockMvc.perform(put("/api/drafts/" + draftId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"body\":\"人工改后的正文\",\"titles\":[\"新标题\"],\"tags\":[\"B\"]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.edited_at").isNotEmpty())
                .andExpect(jsonPath("$.draft.body").value("人工改后的正文"));

        Store.DraftRow d = store.getDraft(draftId).orElseThrow();
        assertTrue(d.payloadJson().contains("人工改后的正文"));
        assertNotNull(d.editedAt(), "应记录编辑时间");
    }

    @Test
    void editDraftCannotBypassQa() throws Exception {
        // 尝试只改 body；qa 必须原样保留（接口不接受 qa 字段）
        mockMvc.perform(put("/api/drafts/" + draftId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"body\":\"改过\",\"qa\":{\"passed\":true,\"issues\":[\"伪造\"]}}"))
                .andExpect(status().isOk());
        Store.DraftRow d = store.getDraft(draftId).orElseThrow();
        assertTrue(d.qaJson().contains("\"passed\":true"), "qa 不应被改写");
        assertTrue(!d.qaJson().contains("伪造"), "不可注入伪造的 qa 内容");
    }

    @Test
    void editMissingDraftReturns404() throws Exception {
        mockMvc.perform(put("/api/drafts/999999")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"body\":\"x\"}"))
                .andExpect(status().isNotFound());
    }

    // ---------------- 导出 ----------------

    @Test
    void exportMarkdownContainsTitleBodyTags() throws Exception {
        MvcResult r = mockMvc.perform(get("/api/drafts/" + draftId + "/export?format=md"))
                .andExpect(status().isOk())
                .andReturn();
        String body = r.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertTrue(body.contains("# 原标题"), "应含主标题");
        assertTrue(body.contains("原正文"), "应含正文");
        assertTrue(body.contains("#标签A"), "应含标签");
        String cd = r.getResponse().getHeader("Content-Disposition");
        assertNotNull(cd);
        assertTrue(cd.contains("attachment"), "应为附件下载");
    }

    @Test
    void exportTxtContainsOnlyTitleAndBody() throws Exception {
        MvcResult r = mockMvc.perform(get("/api/drafts/" + draftId + "/export?format=txt"))
                .andExpect(status().isOk())
                .andReturn();
        String body = r.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertTrue(body.contains("原标题"));
        assertTrue(body.contains("原正文"));
        assertTrue(!body.contains("#标签A"), "txt 不应含标签");
    }

    @Test
    void exportUsesEditedContent() throws Exception {
        mockMvc.perform(put("/api/drafts/" + draftId)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"body\":\"导出应看到这版\"}"));
        MvcResult r = mockMvc.perform(get("/api/drafts/" + draftId + "/export?format=txt"))
                .andReturn();
        String body = r.getResponse().getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertTrue(body.contains("导出应看到这版"), "导出必须是当前生效版本");
    }

    // ---------------- 读单稿（历史稿补齐） ----------------

    @Test
    void getDraftReturnsFullPayloadAndQa() throws Exception {
        mockMvc.perform(get("/api/drafts/" + draftId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.draft.body").value("原正文"))
                .andExpect(jsonPath("$.qa").exists());
    }
}
