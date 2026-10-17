package com.polyface.backend.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.polyface.backend.job.SceneSplitRunner;
import com.polyface.backend.store.Store;

/**
 * 按场景切段接入主流程（`docs/tasks_scene_split.md` T7）。
 *
 * <p><b>不真跑 ffmpeg / Python</b>：子进程那层已经被 Python 侧 29 条单测覆盖；
 * 这里要守住的是 Java 侧的**任务契约** —— 两段式的衔接、明确的失败、素材登记与血缘。
 * 做法：手工摆好假 plan 文件，直接调 Runner 的 {@code complete()}（任务代码路径本体）。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-scenesplit",
        "polyface.media.dir=target/test-data-scenesplit/media",
})
class SceneSplitTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @Autowired
    private SceneSplitRunner sceneSplit;

    private final ObjectMapper mapper = new ObjectMapper();

    @TempDir
    Path tmp;

    private long videoAssetId;
    private String planDir;

    private final Path media = Path.of("target/test-data-scenesplit/media").toAbsolutePath();

    @BeforeEach
    void setUp() throws Exception {
        // 假视频资产：借 asset 表的 rel_path/name；切段阶段校验"文件在磁盘上"，所以要真建一个
        Files.createDirectories(media);
        Files.createDirectories(media.resolve("videos"));
        Files.writeString(media.resolve("videos/fake-src.mp4"), "fake");
        videoAssetId = store.insertAsset("video", "upload", "测试视频", "videos/fake-src.mp4",
                "video/mp4", 1000L, 0, 0, 0d, "", "[]");
        planDir = null;
    }

    /**
     * 摆一份假 plan（方案任务的 work/scene-plan.json），返回方案任务目录。
     *
     * <p>目录必须**在 media 根之内**：plan_url 只为 media 根下的路径生成（putUrl 的边界）。
     */
    private String writeFakePlan(String json) throws Exception {
        Path dir = media.resolve("scenes-test-" + System.nanoTime());
        Path work = dir.resolve("work");
        Files.createDirectories(work);
        Files.writeString(work.resolve("scene-plan.json"), json, StandardCharsets.UTF_8);
        return dir.toString();
    }

    /** 提交一个方案任务记录（不真跑子进程），供切段阶段当 plan_job_id 用。 */
    private long insertPlanJob(String workdir) {
        return store.insertJob("scene_split", "{\"threshold\":27.0}", videoAssetId,
                "videos/fake-src.mp4", workdir, workdir + "/events.jsonl");
    }

    // ---------------- 方案阶段 ----------------

    @Test
    void planPhaseExposesPlanUrlAndCount() throws Exception {
        // 模拟"方案已产出"：plan 文件在 workdir 里，complete() 把它落成 result
        String planJson = """
                {"total_sec":24.0,
                 "segments":[
                   {"index":1,"start":0.0,"end":8.0,"duration":8.0,"thumb":"x.jpg"},
                   {"index":2,"start":8.0,"end":16.0,"duration":8.0},
                   {"index":3,"start":16.0,"end":24.0,"duration":8.0}],
                 "count":3,"raw_cuts":[8.0,16.0],"snapped":0,"speech_aligned":false,
                 "params":{"threshold":27.0,"min_sec":3.0},"detector":"test"}""";
        planDir = writeFakePlan(planJson);
        long jobId = insertPlanJob(planDir);

        sceneSplit.complete(jobId, false, Path.of(planDir), 0, "");

        var j = store.getJob(jobId).orElseThrow();
        assertThat(j.status()).as("方案任务应成功：" + j.error()).isEqualTo("succeeded");
        JsonNode result = mapper.readTree(j.resultJson());
        assertThat(result.path("count").asInt()).isEqualTo(3);
        // DTO 里要给 plan_url（前端渲染缩略图网格就靠它）
        String dto = mockMvc.perform(
                org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .get("/api/jobs/" + jobId))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(dto).contains("plan_url").contains("scene-plan.json");
    }

    // ---------------- 切段阶段的明确失败 ----------------

    @Test
    void cutFailsClearlyWhenPlanFileMissing() {
        // 给了 plan_job_id 但那个任务的目录里**没有** scene-plan.json ——
        // 必须明确失败并说明原因，不能静默产出 0 段。
        String emptyDir = tmp.resolve("empty-" + System.nanoTime()).toString();
        long planJobId = insertPlanJob(emptyDir);
        long jobId = store.insertJob("scene_split",
                "{\"plan_job_id\":" + planJobId + ",\"pick\":[1]}", videoAssetId,
                "videos/fake-src.mp4", emptyDir, emptyDir + "/events-cut.jsonl");

        sceneSplit.complete(jobId, true, Path.of(emptyDir), 0, "");

        var j = store.getJob(jobId).orElseThrow();
        assertThat(j.status()).as("缺 plan 文件必须失败").isEqualTo("failed");
        assertThat(j.error()).contains("scene-plan.json").contains("方案");
    }

    @Test
    void cutWithEmptyPickIsRejectedAtSubmit() throws Exception {
        // 「没勾就全切」是隐式行为 —— 后端必须当场拒绝，而不是替用户决定
        mockMvc.perform(post("/api/jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"kind\":\"scene_split\",\"asset_id\":" + videoAssetId
                                + ",\"plan_job_id\":1,\"pick\":[]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("没有勾选任何段")));
    }

    // ---------------- 素材登记 + 血缘 ----------------

    @Test
    void cutSegmentsRegisteredWithLineage() throws Exception {
        // 摆好方案 + 两个"已切出"的 mp4（真文件，登记要探测它们）
        String planJson = """
                {"total_sec":24.0,
                 "segments":[
                   {"index":1,"start":0.0,"end":8.0,"duration":8.0,
                    "file":"%s"},
                   {"index":2,"start":8.0,"end":16.0,"duration":8.0},
                   {"index":3,"start":16.0,"end":24.0,"duration":8.0,
                    "file":"%s"}],
                 "count":3,"cut":[1,3],
                 "params":{"threshold":27.0,"min_sec":3.0},"detector":"test"}"""
                .formatted(mediaAbs("scene-001.mp4"), mediaAbs("scene-003.mp4"));
        planDir = writeFakePlan(planJson);
        Files.write(Path.of(mediaAbs("scene-001.mp4")), new byte[]{1, 2, 3, 4});
        Files.write(Path.of(mediaAbs("scene-003.mp4")), new byte[]{5, 6, 7, 8});
        long planJobId = insertPlanJob(planDir);
        long jobId = store.insertJob("scene_split",
                "{\"plan_job_id\":" + planJobId + ",\"pick\":[1,3]}", videoAssetId,
                videosPath(), planDir, planDir + "/events-cut.jsonl");

        sceneSplit.complete(jobId, true, Path.of(planDir), 0, "");

        var j = store.getJob(jobId).orElseThrow();
        assertThat(j.status()).as("切段任务应成功：" + j.error()).isEqualTo("succeeded");
        JsonNode result = mapper.readTree(j.resultJson());
        assertThat(result.path("registered")).hasSize(2);

        // 新素材在库里，血缘指向源资产
        List<Long> ids = new java.util.ArrayList<>();
        result.path("registered").forEach(n -> ids.add(n.path("asset_id").asLong()));
        assertThat(ids).hasSize(2);
        var first = store.getAsset(ids.get(0)).orElseThrow();
        assertThat(first.source()).isEqualTo("generated");
        assertThat(first.kind()).isEqualTo("video");
        assertThat(first.name()).contains("第1段").contains("0.00–8.00");
        assertThat(store.linksOf(ids.get(0)))
                .as("切段必须挂回源视频（血缘）")
                .anySatisfy(l -> {
                    assertThat(l.ownerKind()).isEqualTo("asset");
                    assertThat(l.ownerId()).isEqualTo(videoAssetId);
                });
        assertThat(store.linksOf(ids.get(1)))
                .anySatisfy(l -> {
                    assertThat(l.ownerKind()).isEqualTo("asset");
                    assertThat(l.ownerId()).isEqualTo(videoAssetId);
                });
    }

    /** 切出文件的绝对路径（写进 plan 的 file 字段；正斜杠让 JSON 不用转义）。 */
    private String mediaAbs(String name) {
        return media.resolve(name).toAbsolutePath().toString().replace('\\', '/');
    }

    /** 源视频的绝对路径（asset.rel_path 指向的位置；正斜杠让 JSON 不用转义）。 */
    private String videosPath() {
        return media.resolve("videos/fake-src.mp4").toAbsolutePath().toString().replace('\\', '/');
    }

    // ---------------- 续跑不重复登记 ----------------

    @Test
    void resumeDoesNotRegisterDuplicates() throws Exception {
        String planJson = """
                {"total_sec":8.0,
                 "segments":[{"index":1,"start":0.0,"end":8.0,"duration":8.0,"file":"%s"}],
                 "count":1,"cut":[1],"params":{},"detector":"test"}"""
                .formatted(mediaAbs("scene-001.mp4"));
        planDir = writeFakePlan(planJson);
        Files.write(Path.of(mediaAbs("scene-001.mp4")), new byte[]{1, 2, 3});
        long planJobId = insertPlanJob(planDir);
        long jobId = store.insertJob("scene_split",
                "{\"plan_job_id\":" + planJobId + ",\"pick\":[1]}", videoAssetId,
                videosPath(), planDir, planDir + "/events-cut.jsonl");

        sceneSplit.complete(jobId, true, Path.of(planDir), 0, "");
        int afterFirst = resultCount(jobId);

        // 再 complete 一次（模拟续跑重放）—— 不许多出重复素材
        sceneSplit.complete(jobId, true, Path.of(planDir), 0, "");
        assertThat(resultCount(jobId))
                .as("续跑又登记了一遍 —— 比没跑完更糟")
                .isEqualTo(afterFirst);
    }

    private int resultCount(long jobId) throws Exception {
        return mapper.readTree(store.getJob(jobId).orElseThrow().resultJson())
                .path("registered").size();
    }
}
