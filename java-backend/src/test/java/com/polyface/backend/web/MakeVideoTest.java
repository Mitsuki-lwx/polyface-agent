package com.polyface.backend.web;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
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
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 图文成片的入口（`docs/spec_b1.md` T7、`docs/checklist_b1.md` §5）。
 *
 * <p>最要紧的一条：**缺图要当场说清是第几个分镜** —— 不要等合成跑了几分钟才报错，
 * 那时用户已经等了很久、还不知道该改哪儿。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-b1",
        "polyface.media.dir=target/test-data-b1/media",
})
class MakeVideoTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @org.springframework.boot.test.mock.mockito.MockBean
    private PythonClient python;

    private final ObjectMapper mapper = new ObjectMapper();
    private long materialId;
    private long imgA;
    private long imgB;

    @BeforeEach
    void setUp() throws Exception {
        Path dir = Path.of("target/test-data-b1/media/stills");
        Files.createDirectories(dir);
        imgA = still(dir, "a.png");
        imgB = still(dir, "b.png");

        materialId = store.insertMaterial("素材", "长文", "B1 测试", "核心", "理性", "受众", "[]");

        // 一份带剪辑单的成稿（视频类平台的成稿才有 clip_sheet）
        ObjectNode payload = mapper.createObjectNode();
        payload.putArray("titles").add("标题");
        payload.put("body", "正文");
        payload.putArray("tags").add("标签");
        ObjectNode sheet = payload.putObject("clip_sheet");
        sheet.put("intro_note", "前 3 秒是钩子");
        ArrayNode scenes = sheet.putArray("scenes");
        for (int i = 0; i < 2; i++) {
            ObjectNode sc = scenes.addObject();
            sc.put("seq", i + 1);
            sc.put("script", "第" + (i + 1) + "句台词");
            sc.put("subtitle", "第" + (i + 1) + "条字幕");
            sc.put("duration_hint", "3s");
        }
        store.insertDraft(materialId, "douyin", "抖音", "{}", payload.toString(), "{}", "qa_passed");
    }

    private long still(Path dir, String name) throws Exception {
        Path f = dir.resolve(name);
        Files.write(f, ("PNG-fake-" + name).getBytes(StandardCharsets.UTF_8));
        return store.insertAsset("image", "upload", name, "stills/" + name, "image/png",
                Files.size(f), 640, 1136, 0d, "sha-" + name, "");
    }

    private String body(Long... images) throws Exception {
        ObjectNode b = mapper.createObjectNode();
        b.put("kind", "make_video");
        b.put("material_id", materialId);
        b.put("platform", "douyin");
        ArrayNode arr = b.putArray("images");
        for (Long i : images) {
            if (i == null) {
                arr.addNull();
            } else {
                arr.add(i);
            }
        }
        return mapper.writeValueAsString(b);
    }

    @Test
    void namesTheMissingImageIndices() throws Exception {
        // 第 2 个分镜没给图（null）→ 必须指出是"第 2 个"
        mockMvc.perform(post("/api/jobs").contentType(MediaType.APPLICATION_JSON)
                        .content(body(imgA, null)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("[2]")));
    }

    @Test
    void rejectsWrongImageCount() throws Exception {
        mockMvc.perform(post("/api/jobs").contentType(MediaType.APPLICATION_JSON)
                        .content(body(imgA)))          // 2 个分镜只给了 1 张图
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("对不上")));
    }

    @Test
    void rejectsPlatformWithoutClipSheet() throws Exception {
        ObjectNode b = mapper.createObjectNode();
        b.put("kind", "make_video");
        b.put("material_id", materialId);
        b.put("platform", "xhs");                      // 没有这个平台的成稿
        b.putArray("images").add(imgA).add(imgB);

        mockMvc.perform(post("/api/jobs").contentType(MediaType.APPLICATION_JSON)
                        .content(mapper.writeValueAsString(b)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("剪辑单")));
    }

    @Test
    void acceptsWhenImagesMatchScenes() throws Exception {
        mockMvc.perform(post("/api/jobs").contentType(MediaType.APPLICATION_JSON)
                        .content(body(imgA, imgB)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.kind").value("make_video"))
                .andExpect(jsonPath("$.job_id").isNumber());
    }

    @Test
    void planFileIsWrittenIntoTheJobDir() throws Exception {
        String resp = mockMvc.perform(post("/api/jobs").contentType(MediaType.APPLICATION_JSON)
                        .content(body(imgA, imgB)))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        long jobId = mapper.readTree(resp).path("job_id").asLong();

        Path plan = Path.of(store.getJob(jobId).orElseThrow().inputPath());
        org.assertj.core.api.Assertions.assertThat(plan).exists();
        var json = mapper.readTree(Files.readString(plan, StandardCharsets.UTF_8));
        org.assertj.core.api.Assertions.assertThat(json.path("scenes").size()).isEqualTo(2);
        org.assertj.core.api.Assertions.assertThat(json.path("scenes").get(0).path("subtitle").asText())
                .isEqualTo("第1条字幕");
    }
}
