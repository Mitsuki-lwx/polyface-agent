package com.polyface.backend.web;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 自动登记失败**绝不影响主流程**（docs/73 §7）。
 *
 * <p>用替身让登记必然抛错（等价于磁盘不可写/库被锁），断言封面生成与入料仍是 200 ——
 * 登记只是附加价值，不能因为它挂了让用户拿不到封面或入料结果。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "polyface.data-dir=target/test-data-asset-fail")
class AssetRegistrationFailureTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @Autowired
    private MediaDir mediaDir;

    @MockBean
    private PythonClient python;

    @MockBean
    private AssetService assetService;

    private final ObjectMapper mapper = new ObjectMapper();
    private long draftId;

    @BeforeEach
    void setUp() {
        long materialId = store.insertMaterial("素材", "长文", "标题", "核心", "理性", "受众", "[]");
        draftId = store.insertDraft(materialId, "xhs", "小红书", "{}",
                "{\"titles\":[\"草稿标题\"],\"body\":\"正文\"}", "{}", "qa_passed");
    }

    private void stubPythonCoverOk() throws Exception {
        Path png = mediaDir.root().resolve("covers").resolve("draft-fail").resolve("cover.png");
        Files.createDirectories(png.getParent());
        Files.write(png, new byte[]{(byte) 0x89, 'P', 'N', 'G'});
        ObjectNode resp = mapper.createObjectNode();
        resp.put("status", "ok");
        resp.put("path", png.toString());
        resp.put("width", 1080);
        resp.put("height", 1440);
        when(python.composeCover(any())).thenReturn(resp);
    }

    /** 登记抛异常（如目标目录不可写）时，封面仍 200 且 url 正常。 */
    @Test
    void coverSucceedsWhenRegistrationThrows() throws Exception {
        stubPythonCoverOk();
        when(assetService.register(anyString(), anyString(), any(), any()))
                .thenThrow(new RuntimeException("登记目标不可写"));

        mockMvc.perform(post("/api/drafts/" + draftId + "/cover"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ok"))
                .andExpect(jsonPath("$.url").value("/api/media/covers/draft-fail/cover.png"));
    }

    /** 登记成功但 link 抛异常，同样不能影响主流程。 */
    @Test
    void coverSucceedsWhenLinkThrows() throws Exception {
        stubPythonCoverOk();
        when(assetService.register(anyString(), anyString(), any(), any())).thenReturn(1L);
        when(assetService.link(anyLong(), anyString(), anyLong()))
                .thenThrow(new RuntimeException("库被锁"));

        mockMvc.perform(post("/api/drafts/" + draftId + "/cover"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ok"));
    }

    /** 入料登记抛异常时，上传仍 200 且返回 media_id。 */
    @Test
    void ingestUploadSucceedsWhenRegistrationThrows() throws Exception {
        when(assetService.register(anyString(), anyString(), any(), any()))
                .thenThrow(new RuntimeException("登记目标不可写"));

        MockMultipartFile f = new MockMultipartFile("file", "clip.mp4", "video/mp4", "fake-bytes".getBytes());
        mockMvc.perform(multipart("/api/ingest/upload").file(f))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.media_id").exists())
                .andExpect(jsonPath("$.filename").value("clip.mp4"));
    }
}
