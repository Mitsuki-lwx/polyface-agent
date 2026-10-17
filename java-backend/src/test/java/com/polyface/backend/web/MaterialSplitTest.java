package com.polyface.backend.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;

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

    private final ObjectMapper mapper = new ObjectMapper();
    private long parentId;

    @BeforeEach
    void setUp() {
        parentId = store.insertMaterial("我2023年3月裸辞，存款8万够撑14个月。", "长文", "裸辞复盘",
                "裸辞11个月从月入0到3万", "理性干货", "想转自由职业的人",
                "[{\"text\":\"2023年3月裸辞\",\"type\":\"data\"},"
                        + "{\"text\":\"存款8万够撑14个月\",\"type\":\"data\"}]");
    }

    private String req(String anglesJson, String formatsJson) {
        return "{\"angles\":" + anglesJson + ",\"formats\":" + formatsJson + "}";
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

        mockMvc.perform(post("/api/materials/" + parentId + "/split")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(req("[\"钱的视角\",\"时间的视角\"]", "[]")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(2))
                .andExpect(jsonPath("$.items[0].title").value("存款8万的意义"));

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
        mockMvc.perform(post("/api/materials/" + parentId + "/split")
                .contentType(MediaType.APPLICATION_JSON)
                .content(req("[\"钱的视角\",\"时间的视角\"]", "[]"))).andExpect(status().isOk());

        String body = mockMvc.perform(get("/api/materials/" + parentId + "/children"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(body).contains("钱的视角").contains("时间的视角");
    }

    @Test
    void childFactsAreMarkedConfirmed() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());
        mockMvc.perform(post("/api/materials/" + parentId + "/split")
                .contentType(MediaType.APPLICATION_JSON)
                .content(req("[\"钱的视角\",\"时间的视角\"]", "[]"))).andExpect(status().isOk());

        // **不标已确认的话，生成时会拿全文重新理解一遍** —— 这个角度精选的那几条就被丢了。
        // 实测踩到过：子素材只留 3 条事实，出的稿却引用了父素材的 6 条。
        for (var k : store.childrenOf(parentId)) {
            assertThat(k.factsConfirmed()).as("子素材 #" + k.id() + " 的事实应视为已确认").isTrue();
            assertThat(k.factsConfirmedAt()).isNotNull();
        }
    }

    @Test
    void rejectsWhenNeitherAnglesNorFormats() throws Exception {
        mockMvc.perform(post("/api/materials/" + parentId + "/split")
                        .contentType(MediaType.APPLICATION_JSON).content(req("[]", "[]")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("角度或形态")));
    }

    @Test
    void parentMaterialIsNotTouched() throws Exception {
        when(python.splitMaterial(any())).thenReturn(fakeResp());
        var before = store.getMaterial(parentId).orElseThrow();
        mockMvc.perform(post("/api/materials/" + parentId + "/split")
                .contentType(MediaType.APPLICATION_JSON)
                .content(req("[\"钱的视角\",\"时间的视角\"]", "[]"))).andExpect(status().isOk());
        var after = store.getMaterial(parentId).orElseThrow();
        assertThat(after.factsJson()).isEqualTo(before.factsJson());
        assertThat(after.coreMessage()).isEqualTo(before.coreMessage());
        assertThat(after.parentId()).isNull();            // 父素材自己不是谁的孩子
    }

    @Test
    void rejectsUnknownMaterial() throws Exception {
        mockMvc.perform(post("/api/materials/987654/split")
                        .contentType(MediaType.APPLICATION_JSON).content(req("[\"x\"]", "[]")))
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

        mockMvc.perform(post("/api/materials/" + parentId + "/split")
                        .contentType(MediaType.APPLICATION_JSON).content(req("[]", "[\"金句卡片\"]")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].format").value("金句卡片"));

        assertThat(store.childrenOf(parentId).get(0).format()).isEqualTo("金句卡片");
    }
}
