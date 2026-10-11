package com.polyface.backend.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 时间线读写端点（`docs/checklist_timeline.md` §4）。
 *
 * <p>两条要紧的：**校验不通过必须给出是哪一段**（不能只说"格式错误"），
 * 以及**先校验后落盘**（反过来的话，校验失败时磁盘上已经是一份坏数据了）。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-timeline",
        "polyface.media.dir=target/test-data-timeline/media",
})
class TimelineControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @org.springframework.boot.test.mock.mockito.MockBean
    private PythonClient python;

    private final ObjectMapper mapper = new ObjectMapper();
    private long timelineAssetId;
    private Path file;

    @BeforeEach
    void setUp() throws Exception {
        Path dir = Path.of("target/test-data-timeline/media/roughcut");
        Files.createDirectories(dir);
        file = dir.resolve("t-" + System.nanoTime() + ".timeline.json");
        ObjectNode tl = mapper.createObjectNode();
        tl.put("version", 1);
        tl.putObject("source").put("duration", 30.0);
        tl.putArray("keep").addArray().add(0.0).add(10.0);
        tl.putArray("segments");
        Files.writeString(file, mapper.writeValueAsString(tl), StandardCharsets.UTF_8);

        timelineAssetId = store.insertAsset("timeline", "generated", "测试时间线",
                "roughcut/" + file.getFileName(), "application/json",
                Files.size(file), 0, 0, 0d, "deadbeef", "时间线");
    }

    private String body(double keepEnd) throws Exception {
        ObjectNode tl = mapper.createObjectNode();
        tl.put("version", 1);
        tl.putObject("source").put("duration", 30.0);
        tl.putArray("keep").addArray().add(0.0).add(keepEnd);
        tl.putArray("segments");
        ObjectNode req = mapper.createObjectNode();
        req.set("timeline", tl);
        return mapper.writeValueAsString(req);
    }

    @Test
    void getReturnsTimeline() throws Exception {
        mockMvc.perform(get("/api/timelines/" + timelineAssetId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.timeline.version").value(1))
                .andExpect(jsonPath("$.source.duration").value(30.0));
    }

    @Test
    void getRejectsNonTimelineAsset() throws Exception {
        long img = store.insertAsset("image", "upload", "图", "x.png", "image/png",
                1, 1, 1, 0d, "s", "");
        mockMvc.perform(get("/api/timelines/" + img))
                .andExpect(status().isBadRequest());
    }

    @Test
    void getRejectsUnknownAsset() throws Exception {
        mockMvc.perform(get("/api/timelines/987654"))
                .andExpect(status().isNotFound());
    }

    @Test
    void saveSurfacesTheReasonFromPython() throws Exception {
        // Python 说"第 2 段重叠" → 用户必须看到这句，而不是"保存失败"
        when(python.validateTimeline(any()))
                .thenThrow(new RuntimeException("400 Bad Request: {\"detail\":\"第 2 段 [5.0, 15.0] 与上一段重叠\"}"));

        mockMvc.perform(put("/api/timelines/" + timelineAssetId)
                        .contentType(MediaType.APPLICATION_JSON).content(body(10.0)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("第 2 段")));
    }

    @Test
    void saveWritesOnlyAfterValidationPasses() throws Exception {
        when(python.validateTimeline(any())).thenThrow(new RuntimeException("boom"));

        mockMvc.perform(put("/api/timelines/" + timelineAssetId)
                        .contentType(MediaType.APPLICATION_JSON).content(body(9.0)))
                .andExpect(status().isBadRequest());

        // **关键**：校验没过，磁盘上必须还是原来那份（不是坏数据）
        String onDisk = Files.readString(file, StandardCharsets.UTF_8);
        assertThat(onDisk).contains("\"keep\"").contains("10.0");
        assertThat(onDisk).doesNotContain("9.0");
    }

    @Test
    void saveWritesAndRefreshesContentAddressedMeta() throws Exception {
        ObjectNode normalized = mapper.createObjectNode();
        normalized.put("version", 1);
        normalized.putObject("source").put("duration", 30.0);
        normalized.putArray("keep").addArray().add(0.0).add(7.0);
        normalized.putArray("segments");
        ObjectNode resp = mapper.createObjectNode();
        resp.put("ok", true);
        resp.set("normalized", normalized);
        when(python.validateTimeline(any())).thenReturn(resp);

        mockMvc.perform(put("/api/timelines/" + timelineAssetId)
                        .contentType(MediaType.APPLICATION_JSON).content(body(7.0)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.saved").value(true));

        assertThat(Files.readString(file, StandardCharsets.UTF_8)).contains("7.0");
        // 内容寻址：文件改了，sha 必须跟着改（否则摘要与实际文件对不上）
        String sha = store.getAsset(timelineAssetId).orElseThrow().sha256();
        assertThat(sha).isNotEqualTo("deadbeef").hasSize(64);
    }

    @Test
    void validateEndpointProxies() throws Exception {
        ObjectNode ok = mapper.createObjectNode();
        ok.put("ok", true);
        when(python.validateTimeline(any())).thenReturn(ok);

        mockMvc.perform(post("/api/timelines/validate")
                        .contentType(MediaType.APPLICATION_JSON).content(body(10.0)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true));
    }
}
