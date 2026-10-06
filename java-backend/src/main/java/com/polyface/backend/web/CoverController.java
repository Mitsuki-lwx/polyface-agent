package com.polyface.backend.web;

import java.nio.file.Path;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 封面成图（M6-1）：草稿 → 平台封面 PNG。
 *
 * <p>边界：Java 只做「取草稿文案 + 组装请求 + 把产物映射成可访问 url」；
 * 排版与渲染在 Python 侧（gimpish，ADR-019）。工具缺失/渲染失败一律
 * {@code status=needs_manual} 走 200（**降级不是错误**，同 FR-51 入料策略），
 * 只有 Python 连不上才 502。
 */
@RestController
public class CoverController {

    private static final Logger log = LoggerFactory.getLogger(CoverController.class);

    /** 草稿正文兜底标题的截断长度（Python 侧标题上限 60，这里留足余量）。 */
    private static final int TITLE_FALLBACK_CHARS = 30;
    private static final int TITLE_MAX_CHARS = 60;

    private final PythonClient python;
    private final Store store;
    private final MediaDir mediaDir;
    private final AssetService assetService;
    private final ObjectMapper mapper = new ObjectMapper();

    public CoverController(PythonClient python, Store store, MediaDir mediaDir, AssetService assetService) {
        this.python = python;
        this.store = store;
        this.mediaDir = mediaDir;
        this.assetService = assetService;
    }

    /** 请求体可缺省：platform 缺省用草稿自身平台，theme 缺省则不下发（Python 用默认主题）。 */
    public record CoverBody(String platform, String theme) {
    }

    @PostMapping(value = "/api/drafts/{id}/cover", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> cover(@PathVariable long id,
                                          @RequestBody(required = false) CoverBody body) {
        Store.DraftRow draft = store.getDraft(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "draft not found"));

        String platform = (body != null && body.platform() != null && !body.platform().isBlank())
                ? body.platform().trim()
                : draft.platformCode();

        String title = deriveTitle(draft);
        ObjectNode req = mapper.createObjectNode();
        req.put("title", title);
        req.put("platform", platform);
        if (body != null && body.theme() != null && !body.theme().isBlank()) {
            req.put("theme", body.theme().trim());
        }
        // 产物落在媒体根目录下，才能被 GET /api/media/** 托管
        req.put("out_dir", mediaDir.root().resolve("covers").toString());
        req.put("file_stem", "draft-" + id + "-" + platform);

        JsonNode py = forward(() -> python.composeCover(req));

        ObjectNode out = mapper.createObjectNode();
        out.put("status", py.path("status").asText("needs_manual"));
        out.put("path", py.path("path").asText(""));
        out.put("width", py.path("width").asInt(0));
        out.put("height", py.path("height").asInt(0));
        out.put("elapsed_ms", py.path("elapsed_ms").asInt(0));
        out.put("hint", py.path("hint").asText(""));
        String relPath = relPathOf(py.path("path").asText(""));
        if (relPath != null) {
            out.put("url", MediaController.mediaUrl(relPath));
        }
        // 自动登记（docs/73 §7）：登记是附加价值，失败只告警 —— 绝不能因它挂了让封面生成失败
        if ("ok".equals(out.path("status").asText()) && relPath != null) {
            try {
                long assetId = assetService.register(relPath, "generated", title, "封面");
                assetService.link(assetId, "draft", id);
            } catch (Exception e) {
                log.warn("封面资产登记失败（不影响封面生成）draft={} rel={}", id, relPath, e);
            }
        }
        return ResponseEntity.ok(out);
    }

    // ---------------- helpers ----------------

    /**
     * 草稿标题：优先首条标题；为空则退到正文首个非空行的前 {@value #TITLE_FALLBACK_CHARS} 字；
     * 再为空才用素材标题兜底 —— 封面标题不能为空，否则会被 Python 判为非法入参（400）。
     */
    private String deriveTitle(Store.DraftRow draft) {
        String title = "";
        try {
            JsonNode payload = mapper.readTree(draft.payloadJson() == null ? "{}" : draft.payloadJson());
            JsonNode titles = payload.path("titles");
            if (titles.isArray() && titles.size() > 0) {
                title = titles.get(0).asText("").trim();
            }
            if (title.isEmpty()) {
                title = firstLine(payload.path("body").asText(""), TITLE_FALLBACK_CHARS);
            }
        } catch (Exception e) {
            // payload 损坏不该让封面接口整体失败：退回素材标题继续
            log.warn("draft payload 解析失败 id={}", draft.id(), e);
        }
        if (title.isEmpty()) {
            title = store.getMaterial(draft.materialId())
                    .map(m -> m.title() == null ? "" : m.title().trim())
                    .orElse("");
        }
        if (title.isEmpty()) {
            title = "未命名封面";
        }
        return title.length() > TITLE_MAX_CHARS ? title.substring(0, TITLE_MAX_CHARS) : title;
    }

    private static String firstLine(String body, int maxChars) {
        for (String line : body.split("\\R")) {
            String t = line.trim();
            if (!t.isEmpty()) {
                return t.length() > maxChars ? t.substring(0, maxChars) : t;
            }
        }
        return "";
    }

    /** 产物绝对路径 → 相对 media 根的路径；不在媒体目录内则返回 null（不托管根目录外的文件）。 */
    private String relPathOf(String rawPath) {
        if (rawPath == null || rawPath.isBlank()) {
            return null;
        }
        Path abs = Path.of(rawPath).toAbsolutePath().normalize();
        Path root = mediaDir.root();
        if (!abs.startsWith(root)) {
            log.warn("封面产物不在媒体目录内，无法托管：{}", abs);
            return null;
        }
        return root.relativize(abs).toString().replace('\\', '/');
    }

    /** 转发 Python：4xx 原样透传，连接失败 502（照抄 IngestController 的处理风格）。 */
    private JsonNode forward(Supplier<JsonNode> call) {
        try {
            return call.get();
        } catch (RestClientResponseException e) {
            throw new ResponseStatusException(
                    HttpStatus.valueOf(e.getStatusCode().value()),
                    extractDetail(e.getResponseBodyAsString()));
        } catch (Exception e) {
            log.error("cover forward failed", e);
            throw new GlobalExceptionHandler.LlmUnavailableException(e.getMessage());
        }
    }

    private String extractDetail(String body) {
        if (body == null || body.isBlank()) {
            return "封面服务返回错误";
        }
        try {
            String d = mapper.readTree(body).path("detail").asText("");
            return d.isEmpty() ? body : d;
        } catch (Exception e) {
            return body;
        }
    }
}
