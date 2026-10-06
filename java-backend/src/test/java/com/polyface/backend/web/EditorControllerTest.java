package com.polyface.backend.web;

import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
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
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.polyface.backend.media.MediaDir;

/**
 * 内嵌编辑器（M6-2）Java 侧契约测试：可用性降级、入参边界、stop 幂等、字段集合。
 *
 * <p>这里跑的是**真实** {@link com.polyface.backend.editor.EditorProcess} Bean，只把
 * {@code polyface.gimpish.path} 指到不存在的路径 —— 因此全程不会起真进程。
 * 健康检查的成功/超时两条路径由 {@code EditorProcessTest} 用本地 HTTP stub 覆盖
 * （进程探测需要替身，见那里的测试缝）。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-editor",
        "polyface.gimpish.path=target/no-such-gimpish-editor-test",
})
class EditorControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private MediaDir mediaDir;

    /** 有 scene.json 的合法场景目录。 */
    private Path sceneDir;

    @BeforeEach
    void setUp() throws Exception {
        sceneDir = mediaDir.root().resolve("covers").resolve("edit-1");
        Files.createDirectories(sceneDir);
        Files.writeString(sceneDir.resolve("scene.json"), "{\"version\":1}");
    }

    /** ① status：找不到 gimpish → available=false，hint 必须给出可执行的安装命令。 */
    @Test
    void statusReportsUnavailableWithInstallHint() throws Exception {
        mockMvc.perform(get("/api/editor/status"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").value(false))
                .andExpect(jsonPath("$.running").value(false))
                .andExpect(jsonPath("$.url").value(""))
                .andExpect(jsonPath("$.port").value(8765))
                .andExpect(jsonPath("$.scene").value(""))
                .andExpect(jsonPath("$.hint", containsString("npm install -g gimpish")));
    }

    /** ② status 的字段集合固定为 docs/68 §2.1 的 7 个键（前端按名取值，少一个就 undefined）。 */
    @Test
    void statusExposesExactFieldSet() throws Exception {
        mockMvc.perform(get("/api/editor/status"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").exists())
                .andExpect(jsonPath("$.running").exists())
                .andExpect(jsonPath("$.url").exists())
                .andExpect(jsonPath("$.port").exists())
                .andExpect(jsonPath("$.scene").exists())
                .andExpect(jsonPath("$.version").exists())
                .andExpect(jsonPath("$.hint").exists())
                .andExpect(jsonPath("$.*", org.hamcrest.Matchers.hasSize(7)));
    }

    /** ③ open 在没装 gimpish 时仍是 200 + needs_manual（降级不是错误）。 */
    @Test
    void openWithoutGimpishIsNeedsManual200() throws Exception {
        mockMvc.perform(post("/api/editor/open")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"" + jsonEscape(sceneDir) + "\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("needs_manual"))
                .andExpect(jsonPath("$.url").value(""))
                .andExpect(jsonPath("$.port").value(8765))
                .andExpect(jsonPath("$.scene").value(sceneDir.toString()))
                .andExpect(jsonPath("$.hint", containsString("npm install -g gimpish")))
                .andExpect(jsonPath("$.elapsed_ms").exists());
    }

    /** ④ 越界路径 → 400（目录穿越是安全边界，不能当降级）。 */
    @Test
    void openTraversalIs400() throws Exception {
        mockMvc.perform(post("/api/editor/open")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"../outside-scene\"}"))
                .andExpect(status().isBadRequest());

        String outside = mediaDir.root().getParent().resolve("outside-scene").toString();
        mockMvc.perform(post("/api/editor/open")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"" + jsonEscape(Path.of(outside)) + "\"}"))
                .andExpect(status().isBadRequest());
    }

    /** ⑤ 路径合法但不存在 → 404；目录存在但没有 scene.json 也是 404。 */
    @Test
    void openMissingPathIs404() throws Exception {
        Path missing = mediaDir.root().resolve("covers").resolve("nope");
        mockMvc.perform(post("/api/editor/open")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"" + jsonEscape(missing) + "\"}"))
                .andExpect(status().isNotFound());

        Path emptyDir = mediaDir.root().resolve("covers").resolve("edit-empty");
        Files.createDirectories(emptyDir);
        mockMvc.perform(post("/api/editor/open")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"" + jsonEscape(emptyDir) + "\"}"))
                .andExpect(status().isNotFound());
    }

    /** ⑥ 缺 path 是入参非法 → 400。 */
    @Test
    void openWithoutPathIs400() throws Exception {
        mockMvc.perform(post("/api/editor/open")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    /** ⑦ stop 幂等：没在跑也返回 200 + not_running，重复调用结果一致。 */
    @Test
    void stopIsIdempotent200() throws Exception {
        mockMvc.perform(post("/api/editor/stop"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("not_running"));
        mockMvc.perform(post("/api/editor/stop"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("not_running"));
    }

    /** Windows 路径里的反斜杠在 JSON 里必须转义，否则请求体是坏 JSON。 */
    private static String jsonEscape(Path p) {
        return p.toString().replace("\\", "\\\\");
    }
}
