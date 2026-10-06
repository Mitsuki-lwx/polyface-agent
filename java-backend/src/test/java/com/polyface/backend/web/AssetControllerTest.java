package com.polyface.backend.web;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

import org.hamcrest.Matchers;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.polyface.backend.asset.AssetService;
import com.polyface.backend.media.MediaDir;

/**
 * 资产接口契约测试（docs/73 §4）：形状 / 400 / 404 / link 幂等 / url 真能取回字节。
 *
 * <p>重点是**字段名逐字对齐**与 url 的编码往返 —— 前端「素材库」面板直接吃这些字段。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "polyface.data-dir=target/test-data-asset-ctrl")
class AssetControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private AssetService assets;

    @Autowired
    private MediaDir mediaDir;

    private static MockMultipartFile mp(String filename, String contentType, byte[] content) {
        return new MockMultipartFile("file", filename, contentType, content);
    }

    /** 上传一份随机内容，返回资产 id（字符串，便于拼 url）。 */
    private String uploadId(String filename, String contentType, String name) throws Exception {
        String json = mockMvc.perform(multipart("/api/assets/upload")
                        .file(mp(filename, contentType, ("bytes-" + UUID.randomUUID()).getBytes()))
                        .param("name", name))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return new com.fasterxml.jackson.databind.ObjectMapper().readTree(json).path("id").asText();
    }

    /** ① 上传返回完整 DTO 形状；列表带 items/total/limit/offset。 */
    @Test
    void uploadReturnsDtoShapeAndListIsPaginated() throws Exception {
        byte[] bytes = ("png-" + UUID.randomUUID()).getBytes();
        mockMvc.perform(multipart("/api/assets/upload")
                        .file(mp("素材 图.png", "image/png", bytes))
                        .param("name", "素材一")
                        .param("tags", "封面,测试"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").isNumber())
                .andExpect(jsonPath("$.kind").value("image"))
                .andExpect(jsonPath("$.source").value("upload"))
                .andExpect(jsonPath("$.name").value("素材一"))
                .andExpect(jsonPath("$.rel_path", Matchers.startsWith("assets/")))
                .andExpect(jsonPath("$.url", Matchers.startsWith("/api/media/assets/")))
                .andExpect(jsonPath("$.mime").value("image/png"))
                .andExpect(jsonPath("$.size_bytes").value(bytes.length))
                .andExpect(jsonPath("$.duration_sec").value(0.0))
                .andExpect(jsonPath("$.sha256", Matchers.matchesPattern("[0-9a-f]{64}")))
                .andExpect(jsonPath("$.tags[0]").value("封面"))
                .andExpect(jsonPath("$.tags[1]").value("测试"))
                .andExpect(jsonPath("$.created_at").isString())
                .andExpect(jsonPath("$.links").isEmpty());

        mockMvc.perform(get("/api/assets"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isArray())
                .andExpect(jsonPath("$.items[0].id").isNumber())
                .andExpect(jsonPath("$.total").isNumber())
                .andExpect(jsonPath("$.limit").value(50))
                .andExpect(jsonPath("$.offset").value(0));
    }

    /** ② 白名单外扩展名 → 400。 */
    @Test
    void uploadRejectsDisallowedExtension() throws Exception {
        mockMvc.perform(multipart("/api/assets/upload")
                        .file(mp("payload.exe", "application/octet-stream", "x".getBytes())))
                .andExpect(status().isBadRequest());
    }

    /** ③ 不存在的资产：GET / DELETE / link 目标都是 404。 */
    @Test
    void missingAssetReturns404() throws Exception {
        mockMvc.perform(get("/api/assets/999999")).andExpect(status().isNotFound());
        mockMvc.perform(delete("/api/assets/999999")).andExpect(status().isNotFound());
        mockMvc.perform(post("/api/assets/999999/link")
                        .contentType("application/json")
                        .content("{\"owner_kind\":\"draft\",\"owner_id\":1}"))
                .andExpect(status().isNotFound());
    }

    /** ④ link 幂等：重复调用仍 200、links 不重复增长；非法 owner_kind → 400。 */
    @Test
    void linkIsIdempotent() throws Exception {
        String id = uploadId("link.png", "image/png", "链");
        String body = "{\"owner_kind\":\"draft\",\"owner_id\":7}";

        mockMvc.perform(post("/api/assets/" + id + "/link").contentType("application/json").content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true))
                .andExpect(jsonPath("$.links", Matchers.hasSize(1)))
                .andExpect(jsonPath("$.links[0].owner_kind").value("draft"))
                .andExpect(jsonPath("$.links[0].owner_id").value(7));

        mockMvc.perform(post("/api/assets/" + id + "/link").contentType("application/json").content(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.links", Matchers.hasSize(1)));

        // 同资产可挂多处（素材复用的字面含义）
        mockMvc.perform(post("/api/assets/" + id + "/link").contentType("application/json")
                        .content("{\"owner_kind\":\"material\",\"owner_id\":3}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.links", Matchers.hasSize(2)));

        // owner_kind 只认 material | draft（standalone 只是"没有 link"的语义）
        mockMvc.perform(post("/api/assets/" + id + "/link").contentType("application/json")
                        .content("{\"owner_kind\":\"standalone\",\"owner_id\":1}"))
                .andExpect(status().isBadRequest());
        mockMvc.perform(post("/api/assets/" + id + "/link").contentType("application/json")
                        .content("{\"owner_kind\":\"draft\"}"))
                .andExpect(status().isBadRequest());
    }

    /** ⑤ 删除返回三态标记：解链数 + 是否真删了文件；之后 GET 404。 */
    @Test
    void deleteReportsFlagsAndRemovesRecord() throws Exception {
        String id = uploadId("del.png", "image/png", "待删");
        mockMvc.perform(post("/api/assets/" + id + "/link").contentType("application/json")
                        .content("{\"owner_kind\":\"draft\",\"owner_id\":9}"))
                .andExpect(status().isOk());

        mockMvc.perform(delete("/api/assets/" + id))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true))
                .andExpect(jsonPath("$.file_removed").value(true))
                .andExpect(jsonPath("$.links_removed").value(1));

        mockMvc.perform(get("/api/assets/" + id)).andExpect(status().isNotFound());
    }

    /** ⑥ url 真能取回字节，且**含空格的路径**逐段编码成 %20。 */
    @Test
    void urlServesBytesForPathWithSpaces() throws Exception {
        byte[] bytes = ("cover-" + UUID.randomUUID()).getBytes();
        Path png = mediaDir.root().resolve("covers").resolve("draft 1").resolve("cover.png");
        Files.createDirectories(png.getParent());
        Files.write(png, bytes);

        long id = assets.register("covers/draft 1/cover.png", "generated", "含空格封面", "封面");
        AssetService.AssetDto dto = assets.toDto(assets.get(id).orElseThrow());
        if (!"/api/media/covers/draft%201/cover.png".equals(dto.url())) {
            throw new AssertionError("url 编码不正确：" + dto.url());
        }

        // 该 url 必须真的能取回原字节（否则前端 <img> 是死链）
        mockMvc.perform(get(URI.create(dto.url())))
                .andExpect(status().isOk())
                .andExpect(content().bytes(bytes));

        // 同一个 url 也应能从 DTO 端点拿到
        mockMvc.perform(get("/api/assets/" + id))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.url").value("/api/media/covers/draft%201/cover.png"))
                .andExpect(jsonPath("$.kind").value("image"))
                .andExpect(jsonPath("$.source").value("generated"))
                .andExpect(jsonPath("$.links").isEmpty());
    }

    /** ⑦ kind 多值筛选、q 模糊搜索、limit 上限 200。 */
    @Test
    void listSupportsKindFilterSearchAndLimitCap() throws Exception {
        String tag = UUID.randomUUID().toString().substring(0, 8);
        String imgId = uploadId("k1.png", "image/png", "筛选图-" + tag);
        String audioId = uploadId("k2.mp3", "audio/mpeg", "筛选音-" + tag);

        mockMvc.perform(get("/api/assets").param("kind", "audio").param("q", tag))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", Matchers.hasSize(1)))
                .andExpect(jsonPath("$.items[0].id").value(Integer.parseInt(audioId)))
                .andExpect(jsonPath("$.items[0].kind").value("audio"));

        mockMvc.perform(get("/api/assets").param("kind", "image,audio").param("q", tag))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", Matchers.hasSize(2)));

        // limit 上限 200；两条都命中（imgId/audioId 都在结果里，顺序为 id DESC 最新在前）
        mockMvc.perform(get("/api/assets").param("q", tag).param("limit", "999"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.limit").value(200))
                .andExpect(jsonPath("$.items", Matchers.hasSize(2)))
                .andExpect(jsonPath("$.items[*].id",
                        Matchers.containsInAnyOrder(Integer.parseInt(imgId), Integer.parseInt(audioId))));
    }
}
