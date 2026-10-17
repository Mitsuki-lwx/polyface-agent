package com.polyface.backend.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.util.List;

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
 * 素材拆分端点（`docs/spec_split.md`）。
 *
 * <p>最要紧的一条：拆出来的东西**落成真正的素材**（进 material 表、带血缘），
 * 而不是几条死稿子 —— 否则"一次创作多次使用"就不成立。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-split",
        "polyface.media.dir=target/test-data-split/media",
})
class MaterialSplitTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @org.springframework.boot.test.mock.mockito.MockBean
    private PythonClient python;

    @Autowired
    private com.polyface.backend.job.SplitRunner splitRunner;

    private final ObjectMapper mapper = new ObjectMapper();
    private long parentId;

    /** 取消场景用：mock 回调里要能拿到"当前任务 id"。 */
    private volatile long cancelJobId = -1;

    @BeforeEach
    void setUp() {
        parentId = store.insertMaterial("我2023年3月裸辞，存款8万够撑14个月。", "长文", "裸辞复盘",
                "裸辞11个月从月入0到3万", "理性干货", "想转自由职业的人",
                "[{\"text\":\"2023年3月裸辞\",\"type\":\"data\"},"
                        + "{\"text\":\"存款8万够撑14个月\",\"type\":\"data\"}]");
    }

    /** 同步跑一次拆分任务 —— 走的是**真正的任务代码路径**，不是绕过它。 */
    private void runSplit(long materialId, String anglesJson, String formatsJson)
            throws Exception {
        List<String> angles = parseList(anglesJson);
        List<String> formats = parseList(formatsJson);
        var dir = java.nio.file.Files.createTempDirectory("split-test");
        long jobId = store.insertJob("split", "{}", null, String.valueOf(materialId),
                dir.toString(), dir.resolve("events.jsonl").toString());
        splitRunner.execute(jobId, materialId, angles, formats, dir);
        var j = store.getJob(jobId).orElseThrow();
        assertThat(j.status()).as("任务应成功：" + j.error()).isEqualTo("succeeded");
    }

    private static List<String> parseList(String json) throws Exception {
        List<String> out = new java.util.ArrayList<>();
        for (var n : new ObjectMapper().readTree(json)) {
            out.add(n.asText());
        }
        return out;
    }

    /** 假的拆分响应：两条，事实按序号从父素材取（与真实契约一致）。 */
    private ObjectNode fakeResp() {
        ObjectNode resp = mapper.createObjectNode();
        ArrayNode items = resp.putArray("items");
        ObjectNode a = items.addObject();
        a.put("angle", "钱的视角").put("format", "").put("title", "存款8万的意义").put("core_message", "先算账");
        a.putArray("facts").addObject().put("text", "存款8万够撑14个月").put("type", "data").put("from_fact", 2);
        ObjectNode b = items.addObject();
        b.put("angle", "时间的视角").put("format", "").put("title", "11个月").put("core_message", "时间线");
        b.putArray("facts");
        return resp;
    }

    @Test
    void childrenAreRealMaterialsWithLineage() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());

        runSplit(parentId, "[\"钱的视角\",\"时间的视角\"]", "[]");

        // **关键**：落成了真正的素材，不是稿子
        var kids = store.childrenOf(parentId);
        assertThat(kids).hasSize(2);
        assertThat(kids.get(0).angle()).isEqualTo("钱的视角");
        assertThat(kids.get(0).parentId()).isEqualTo(parentId);
        assertThat(kids.get(0).sourceKind()).isEqualTo("split");
        // 子素材带自己的事实清单（从父素材那条取来的原文）
        assertThat(kids.get(0).factsJson()).contains("存款8万够撑14个月");
        // 而且它是"素材"不是"稿子"：能再走一遍生成（这里只验它有 id、有正文）
        assertThat(kids.get(0).rawText()).isEqualTo(store.getMaterial(parentId).orElseThrow().rawText());
        assertThat(kids.get(0).id()).isNotEqualTo(parentId);
    }

    @Test
    void parentCanListItsChildren() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());
        runSplit(parentId, "[\"钱的视角\",\"时间的视角\"]", "[]");

        String body = mockMvc.perform(get("/api/materials/" + parentId + "/children"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(body).contains("钱的视角").contains("时间的视角");
    }

    @Test
    void childFactsAreMarkedConfirmed() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());
        runSplit(parentId, "[\"钱的视角\",\"时间的视角\"]", "[]");

        // **不标已确认的话，生成时会拿全文重新理解一遍** —— 这个角度精选的那几条就被丢了。
        // 实测踩到过：子素材只留 3 条事实，出的稿却引用了父素材的 6 条。
        for (var k : store.childrenOf(parentId)) {
            assertThat(k.factsConfirmed()).as("子素材 #" + k.id() + " 的事实应视为已确认").isTrue();
            assertThat(k.factsConfirmedAt()).isNotNull();
        }
    }

    @Test
    void resumeDoesNotCreateDuplicateChildren() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());
        var dir = java.nio.file.Files.createTempDirectory("split-resume");
        long jobId = store.insertJob("split", "{}", null, String.valueOf(parentId),
                dir.toString(), dir.resolve("events.jsonl").toString());
        var angles = List.of("钱的视角", "时间的视角");
        splitRunner.execute(jobId, parentId, angles, List.of(), dir);
        assertThat(store.childrenOf(parentId)).hasSize(2);

        // 同一个任务再跑一次（模拟"被打断后点继续"）—— **绝不能多出一批重复素材**
        splitRunner.execute(jobId, parentId, angles, List.of(), dir);
        assertThat(store.childrenOf(parentId))
                .as("续跑又建了一遍子素材 —— 这比没跑完更糟").hasSize(2);
        // 而且**没有再调一次模型**（记录已存在就复用）
        org.mockito.Mockito.verify(python, org.mockito.Mockito.times(1)).splitMaterial(any());
    }

    @Test
    void cancelDuringCallDiscardsResult() throws Exception {
        // 调用**中途**取消：在途 HTTP 拦不住，但结果必须丢掉 ——
        // 不然用户取消了却多出一批素材，比取消不了还糟。
        when(python.splitMaterial(any())).thenAnswer(inv -> {
            splitRunner.cancel(cancelJobId);      // 模拟"调用进行中，用户点了取消"
            return fakeResp();
        });
        var dir = java.nio.file.Files.createTempDirectory("split-cancel");
        long jobId = store.insertJob("split", "{}", null, String.valueOf(parentId),
                dir.toString(), dir.resolve("events.jsonl").toString());
        cancelJobId = jobId;
        splitRunner.execute(jobId, parentId, List.of("钱的视角"), List.of(), dir);
        assertThat(store.getJob(jobId).orElseThrow().status()).isEqualTo("canceled");
        assertThat(store.childrenOf(parentId)).as("取消后不该建出任何子素材").isEmpty();
    }

    @Test
    void rejectsWhenNeitherAnglesNorFormats() throws Exception {
        // 校验在**提交时**（不该变成一条失败的任务让用户去任务列表里找）
        mockMvc.perform(post("/api/jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"kind\":\"split\",\"material_id\":" + parentId
                                + ",\"angles\":[],\"formats\":[]}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("角度或形态")));
    }

    @Test
    void parentMaterialIsNotTouched() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());
        var before = store.getMaterial(parentId).orElseThrow();
        runSplit(parentId, "[\"钱的视角\",\"时间的视角\"]", "[]");
        var after = store.getMaterial(parentId).orElseThrow();
        assertThat(after.factsJson()).isEqualTo(before.factsJson());
        assertThat(after.coreMessage()).isEqualTo(before.coreMessage());
        assertThat(after.parentId()).isNull();            // 父素材自己不是谁的孩子
    }

    @Test
    void rejectsUnknownMaterial() throws Exception {
        mockMvc.perform(post("/api/jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"kind\":\"split\",\"material_id\":987654,"
                                + "\"angles\":[\"x\"],\"formats\":[]}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void formatsOnlyAlsoWorks() throws Exception {
        ObjectNode resp = mapper.createObjectNode();
        ArrayNode items = resp.putArray("items");
        ObjectNode a = items.addObject();
        a.put("angle", "").put("format", "金句卡片").put("title", "金句").put("core_message", "c");
        a.putArray("facts");
        when(python.splitMaterial(any())).thenReturn(resp);

        runSplit(parentId, "[]", "[\"金句卡片\"]");

        assertThat(store.childrenOf(parentId).get(0).format()).isEqualTo("金句卡片");
    }
}
