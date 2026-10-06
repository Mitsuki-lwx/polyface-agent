package com.polyface.backend.web;

import java.nio.file.Files;
import java.nio.file.Path;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.editor.EditorProcess;
import com.polyface.backend.media.MediaDir;

/**
 * 内嵌编辑器（M6-2）的三个端点：status / open / stop（字段名见 {@code docs/68} §2，逐字对齐）。
 *
 * <p>边界：本类只做「校验入参 + 把 {@link EditorProcess} 的结果翻成 JSON」；
 * 进程编排与降级判定都在 {@link EditorProcess}。**只有入参非法才是 400/404**，
 * 工具缺失/端口被占/启动超时一律 200 + {@code needs_manual}（docs/68 §2.2）。
 */
@RestController
public class EditorController {

    private final EditorProcess editor;
    private final MediaDir mediaDir;
    private final ObjectMapper mapper = new ObjectMapper();

    public EditorController(EditorProcess editor, MediaDir mediaDir) {
        this.editor = editor;
        this.mediaDir = mediaDir;
    }

    /** 请求体 {@code {"path": "..."}}；path 可以是封面目录，也可以是目录里的 scene.json。 */
    public record OpenBody(String path) {
    }

    @GetMapping(value = "/api/editor/status", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> status() {
        EditorProcess.Status s = editor.status();
        ObjectNode out = mapper.createObjectNode();
        out.put("available", s.available());
        out.put("running", s.running());
        out.put("url", s.url());
        out.put("port", s.port());
        out.put("scene", s.scene());
        out.put("version", s.version());
        out.put("hint", s.hint());
        return ResponseEntity.ok(out);
    }

    @PostMapping(value = "/api/editor/open", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> open(@RequestBody(required = false) OpenBody body) {
        String raw = body == null ? null : body.path();
        if (raw == null || raw.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 path");
        }
        // 一律经 MediaDir.resolveSafe（含 '\' 归一化）：越界 → 400。
        // 前端传的是 M6-1 产物目录的绝对路径，只要落在媒体根内就放行（docs/68 §5）。
        Path safe = mediaDir.resolveSafe(raw);
        Path dir = sceneDirOf(safe);

        EditorProcess.OpenResult r = editor.open(dir);
        ObjectNode out = mapper.createObjectNode();
        out.put("status", r.status());
        out.put("url", r.url());
        out.put("port", r.port());
        out.put("scene", r.scene());
        out.put("version", r.version());
        out.put("hint", r.hint());
        out.put("elapsed_ms", r.elapsedMs());
        return ResponseEntity.ok(out);
    }

    @PostMapping(value = "/api/editor/stop", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> stop() {
        boolean stopped = editor.stop();
        ObjectNode out = mapper.createObjectNode();
        out.put("status", stopped ? "stopped" : "not_running");
        return ResponseEntity.ok(out);
    }

    /**
     * 入参 → 场景目录：目录内必须有 {@code scene.json}；给的是 scene.json 本身则取其父目录。
     * 其余情形（不存在、非 scene.json 的文件）都是 404 —— 路径本身合法，只是没有场景可开。
     */
    private static Path sceneDirOf(Path safe) {
        if (Files.isDirectory(safe)) {
            if (!Files.isRegularFile(safe.resolve("scene.json"))) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "目录内没有 scene.json");
            }
            return safe;
        }
        if (Files.isRegularFile(safe) && "scene.json".equals(safe.getFileName().toString())) {
            return safe.getParent();
        }
        throw new ResponseStatusException(HttpStatus.NOT_FOUND, "路径不存在或不是场景目录");
    }
}
