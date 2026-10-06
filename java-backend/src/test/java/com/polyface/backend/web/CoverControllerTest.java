package com.polyface.backend.web;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.client.ResourceAccessException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 封面成图（M6-1）Java 侧契约测试：透传、降级、不可达、媒体托管与目录穿越。
 *
 * <p>Python 用替身（{@code @MockBean}），只验证 Java 边界——真正的渲染由 Python 单测与
 * 浏览器端到端覆盖。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "polyface.data-dir=target/test-data-cover")
class CoverControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @Autowired
    private MediaDir mediaDir;

    @org.springframework.boot.test.mock.mockito.MockBean
    private PythonClient python;

    private final ObjectMapper mapper = new ObjectMapper();
    private long draftId;

    @BeforeEach
    void setUp() {
        long materialId = store.insertMaterial("素材原文：2023年裸辞。", "长文", "素材标题",
                "核心", "理性", "受众", "[]");
        ObjectNode payload = mapper.createObjectNode();
        payload.putArray("titles").add("草稿标题");
        payload.put("body", "正文第一行\n正文第二行");
        payload.putArray("tags").add("标签");
        draftId = store.insertDraft(materialId, "xhs", "小红书", "{}",
                payload.toString(), "{\"passed\":true,\"issues\":[],\"warnings\":[]}", "qa_passed");
    }

    /** ① ok 透传：url 正确、请求体契约正确、且能 GET 到原始字节。 */
    @Test
    void okPassesThroughAndServesBytes() throws Exception {
        Path png = mediaDir.root().resolve("covers").resolve("draft-x").resolve("cover.png");
        Files.createDirectories(png.getParent());
        byte[] bytes = {(byte) 0x89, 'P', 'N', 'G', 1, 2, 3};
        Files.write(png, bytes);

        ObjectNode resp = mapper.createObjectNode();
        resp.put("status", "ok");
        resp.put("editor", "gimpish");
        resp.put("path", png.toString());
        resp.put("scene_path", png.getParent().resolve("scene.json").toString());
        resp.put("width", 1080);
        resp.put("height", 1440);
        resp.put("elapsed_ms", 512);
        resp.put("hint", "");
        when(python.composeCover(any())).thenReturn(resp);

        mockMvc.perform(post("/api/drafts/" + draftId + "/cover"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ok"))
                .andExpect(jsonPath("$.url").value("/api/media/covers/draft-x/cover.png"))
                .andExpect(jsonPath("$.width").value(1080))
                .andExpect(jsonPath("$.height").value(1440))
                .andExpect(jsonPath("$.elapsed_ms").value(512));

        // 转发给 Python 的请求体必须符合冻结契约
        ArgumentCaptor<JsonNode> captor = ArgumentCaptor.forClass(JsonNode.class);
        org.mockito.Mockito.verify(python).composeCover(captor.capture());
        JsonNode sent = captor.getValue();
        org.junit.jupiter.api.Assertions.assertEquals("草稿标题", sent.path("title").asText());
        org.junit.jupiter.api.Assertions.assertEquals("xhs", sent.path("platform").asText());
        org.junit.jupiter.api.Assertions.assertEquals(
                mediaDir.root().resolve("covers").toString(), sent.path("out_dir").asText());
        org.junit.jupiter.api.Assertions.assertEquals(
                "draft-" + draftId + "-xhs", sent.path("file_stem").asText());

        // 生成的 url 必须真的能取回字节（否则前端 <img> 是死链）
        mockMvc.perform(get("/api/media/covers/draft-x/cover.png"))
                .andExpect(status().isOk())
                .andExpect(header().string("Content-Type", "image/png"))
                .andExpect(content().bytes(bytes));
    }

    /** ② needs_manual 走 200 且带 hint（降级不是错误）。 */
    @Test
    void needsManualIsTwoHundredWithHint() throws Exception {
        ObjectNode resp = mapper.createObjectNode();
        resp.put("status", "needs_manual");
        resp.put("editor", "gimpish");
        resp.put("path", "");
        resp.put("scene_path", "");
        resp.put("width", 1080);
        resp.put("height", 1440);
        resp.put("elapsed_ms", 3);
        resp.put("hint", "未找到 gimpish，请安装后重试");
        when(python.composeCover(any())).thenReturn(resp);

        mockMvc.perform(post("/api/drafts/" + draftId + "/cover"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("needs_manual"))
                .andExpect(jsonPath("$.hint").value("未找到 gimpish，请安装后重试"))
                .andExpect(jsonPath("$.url").doesNotExist());
    }

    /** ③ Python 不可达 → 502。 */
    @Test
    void pythonUnreachableReturns502() throws Exception {
        when(python.composeCover(any()))
                .thenThrow(new ResourceAccessException("Connection refused"));
        mockMvc.perform(post("/api/drafts/" + draftId + "/cover"))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error").value("llm_service_unavailable"));
    }

    /** ④ 目录穿越必须被拒（不能把根目录外的文件当媒体读出）。 */
    @Test
    void directoryTraversalIsRejected() throws Exception {
        // 在媒体根目录之外放一个"机密"文件，确认拿不到
        Path outside = mediaDir.root().getParent().resolve("secret-outside.txt");
        Files.writeString(outside, "top-secret");

        mockMvc.perform(get("/api/media/../../etc/passwd"))
                .andExpect(status().is4xxClientError());
        mockMvc.perform(get("/api/media/%2e%2e%2fsecret-outside.txt"))
                .andExpect(status().is4xxClientError());
        mockMvc.perform(get("/api/media/..%2fsecret-outside.txt"))
                .andExpect(status().is4xxClientError());
    }

    /** 不存在的文件 404（而非 500）。 */
    @Test
    void missingMediaReturns404() throws Exception {
        mockMvc.perform(get("/api/media/covers/nope/cover.png"))
                .andExpect(status().isNotFound());
    }
}
