package com.polyface.backend.web;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.media.MediaDir;

/**
 * 音视频入料（FR-51）：受理本机媒体文件 → 转发 Python 探测/转写 → 返回文字。
 *
 * <p>边界说明：本控制器**只负责"得到文字"**。拿到文本后由前端调用既有的
 * {@code POST /api/materials} 创建素材，完全复用 understand 解析与后续生成链路。
 *
 * <p>合规：文件仅存本机 {@code data/media}；不上传任何数据；不自动发布（ADR-010）。
 */
@RestController
public class IngestController {

    private static final Logger log = LoggerFactory.getLogger(IngestController.class);

    /** 允许的媒体扩展名（白名单，避免受理任意文件）。 */
    private static final Set<String> ALLOWED_EXT = Set.of(
            "mp4", "mov", "mkv", "avi", "webm", "flv", "m4v", "wmv", "mpg", "mpeg", "ts",
            "mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma");

    private final PythonClient python;
    private final ObjectMapper mapper = new ObjectMapper();
    private final Path mediaDir;

    public IngestController(PythonClient python, MediaDir mediaDir) {
        this.python = python;
        // 媒体目录的唯一来源见 MediaDir：与封面产物 / /api/media 托管共用同一根目录
        this.mediaDir = mediaDir.root();
    }

    // ---------------- DTO ----------------
    public record IngestBody(String media_id, String path, String mode) {
    }

    // ---------------- 上传 ----------------
    @PostMapping(value = "/api/ingest/upload", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> upload(@RequestParam("file") MultipartFile file) throws IOException {
        if (file == null || file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "未选择文件或文件为空");
        }
        String original = file.getOriginalFilename() == null ? "" : file.getOriginalFilename();
        String ext = extOf(original);
        if (!ALLOWED_EXT.contains(ext)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "不支持的扩展名：" + (ext.isEmpty() ? "(无)" : ext));
        }
        String mediaId = UUID.randomUUID().toString().replace("-", "") + "." + ext;
        Path dest = mediaDir.resolve(mediaId);
        file.transferTo(dest);
        log.info("media uploaded: {} ({} bytes) from {}", mediaId, file.getSize(), original);

        ObjectNode out = mapper.createObjectNode();
        out.put("media_id", mediaId);
        out.put("filename", original);
        out.put("size", file.getSize());
        out.put("path", dest.toString());
        return ResponseEntity.ok(out);
    }

    // ---------------- 探测 ----------------
    @PostMapping(value = "/api/ingest/probe", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode probe(@RequestBody IngestBody body) {
        Path target = resolve(body);
        JsonNode py = forward(() -> python.probe(target.toString()));
        ObjectNode out = py.deepCopy();
        out.put("media_id", mediaIdOf(target));
        return out;
    }

    // ---------------- 转写 / 提取 ----------------
    @PostMapping(value = "/api/ingest/transcribe", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode transcribe(@RequestBody IngestBody body) {
        Path target = resolve(body);
        JsonNode py = forward(() -> python.transcribe(target.toString(), body.mode()));
        ObjectNode out = py.deepCopy();
        out.put("media_id", mediaIdOf(target));
        out.put("filename", target.getFileName().toString());
        return out;
    }

    // ---------------- 清理媒体副本 ----------------
    @DeleteMapping("/api/ingest/media/{mediaId}")
    public ResponseEntity<Void> deleteMedia(@PathVariable String mediaId) throws IOException {
        Path target = resolve(new IngestBody(mediaId, null, null));
        Files.deleteIfExists(target);
        log.info("media removed: {}", mediaId);
        return ResponseEntity.noContent().build();
    }

    // ---------------- helpers ----------------

    /** 解析目标文件：media_id（本机副本）或 path（用户指定路径），两者都做白名单校验。 */
    private Path resolve(IngestBody body) {
        if (body != null && body.media_id() != null && !body.media_id().isBlank()) {
            String id = body.media_id().trim();
            if (id.contains("/") || id.contains("\\") || id.contains("..")) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "非法的 media_id");
            }
            Path p = mediaDir.resolve(id).normalize();
            if (!p.startsWith(mediaDir)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "非法的 media_id");
            }
            if (!Files.isRegularFile(p)) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "媒体副本不存在，请重新上传");
            }
            return p;
        }
        String raw = body == null ? null : body.path();
        if (raw == null || raw.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请提供 media_id 或 path");
        }
        Path p = Path.of(raw.trim());
        if (!Files.exists(p)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "文件不存在：" + raw);
        }
        if (Files.isDirectory(p)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "路径是目录，请指定具体文件");
        }
        if (!Files.isRegularFile(p)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "不是普通文件");
        }
        String ext = extOf(p.getFileName().toString());
        if (!ALLOWED_EXT.contains(ext)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "不支持的扩展名：" + (ext.isEmpty() ? "(无)" : ext));
        }
        return p;
    }

    /** 转发 Python；把 Python 的 4xx 原样透传，连接失败则 502。 */
    private JsonNode forward(java.util.function.Supplier<JsonNode> call) {
        try {
            return call.get();
        } catch (RestClientResponseException e) {
            String detail = extractDetail(e.getResponseBodyAsString());
            throw new ResponseStatusException(
                    HttpStatus.valueOf(e.getStatusCode().value()), detail);
        } catch (Exception e) {
            log.error("ingest forward failed", e);
            throw new GlobalExceptionHandler.LlmUnavailableException(e.getMessage());
        }
    }

    private String extractDetail(String body) {
        if (body == null || body.isBlank()) {
            return "入料服务返回错误";
        }
        try {
            String d = mapper.readTree(body).path("detail").asText("");
            return d.isEmpty() ? body : d;
        } catch (Exception e) {
            return body;
        }
    }

    private String mediaIdOf(Path p) {
        return p.getParent() != null && p.getParent().equals(mediaDir)
                ? p.getFileName().toString() : null;
    }

    private static String extOf(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot < 0 ? "" : filename.substring(dot + 1).toLowerCase();
    }
}
