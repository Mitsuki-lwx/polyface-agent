package com.polyface.backend.web;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;

/**
 * 模板管理 API（FR-62）+ 示例学习（FR-63）。
 * - CRUD：新建/编辑/复制/删除/列表/详情/导出/导入；导入冲突不自动决策（409 + 冲突清单）
 * - 学习：示例文本 → 草稿模板（status=draft），须人工 activate 后才可用于生成
 */
@RestController
public class TemplateController {

    /** 示例文本最短长度：太短无法拆出结构。 */
    private static final int MIN_SAMPLE_CHARS = 50;

    private static final Logger log = LoggerFactory.getLogger(TemplateController.class);
    private final Store store;
    private final PythonClient python;
    private final ObjectMapper mapper = new ObjectMapper();

    public TemplateController(Store store, PythonClient python) {
        this.store = store;
        this.python = python;
    }

    // ---------------- DTO ----------------
    public record TemplateBody(
            String kind,
            @NotBlank String name,
            String voice,
            String opening,
            List<String> structure,
            String closing,
            String tag_style,
            List<String> taboo) {
    }

    public record LearnBody(String sample_text, String source_note) {
    }

    // ---------------- 列表 ----------------
    /**
     * 列表。status 默认 {@code active}（安全默认，符合 UC-16：草稿不进生成选项）。
     * 传 {@code all} 返回全部（模板库管理界面用）。
     */
    @GetMapping(value = "/api/templates", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode list(@RequestParam(required = false) String kind,
                         @RequestParam(required = false, defaultValue = "active") String status) {
        String statusFilter = "all".equalsIgnoreCase(status) || status == null || status.isBlank()
                ? null : status;
        ObjectNode out = mapper.createObjectNode();
        ArrayNode arr = out.putArray("templates");
        for (Store.TemplateRow t : store.listTemplates(kind, statusFilter)) {
            arr.add(TemplateMapper.toJson(t));
        }
        return out;
    }

    // ---------------- FR-63 示例学习 ----------------
    /**
     * 示例文本 → 拆解为草稿模板。
     * 产物 status=draft，**不会**出现在生成页选择器；须调用 activate 确认启用。
     * 不保存示例原文，只落拆解结果 + source_note。
     */
    @PostMapping(value = "/api/templates/learn", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> learn(@RequestBody LearnBody body) {
        String sample = body == null ? null : body.sample_text();
        if (sample == null || sample.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "示例文本不能为空");
        }
        String text = sample.trim();
        if (text.length() < MIN_SAMPLE_CHARS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "示例文本过短，至少 " + MIN_SAMPLE_CHARS + " 字才能拆解出结构");
        }

        JsonNode resp;
        try {
            resp = python.learn(text, body.source_note());
        } catch (Exception ex) {
            log.error("learn failed", ex);
            throw new GlobalExceptionHandler.LlmUnavailableException(ex.getMessage());
        }

        JsonNode learned = resp.path("template");
        String sourceNote = body.source_note() == null ? null : body.source_note().trim();
        long id = store.insertTemplate(
                "content",
                learned.path("name").asText("学习：示例"),
                learned.path("voice").asText(""),
                learned.path("opening").asText(""),
                learned.path("structure").toString(),
                learned.path("closing").asText(""),
                learned.path("tag_style").asText(""),
                learned.path("taboo").toString(),
                false, null, 1,
                "draft",                       // 关键：一律草稿态（ADR-014 / UC-16）
                sourceNote);

        Store.TemplateRow row = requireTemplate(id);
        ObjectNode out = TemplateMapper.toJson(row);
        out.put("used_mock", resp.path("used_mock").asBoolean(false));
        out.put("rationale", learned.path("rationale").asText(""));
        log.info("template learned id={} name={} structure={} (status=draft)",
                id, row.name(), learned.path("structure").size());
        return ResponseEntity.status(HttpStatus.CREATED).body(out);
    }

    /** 确认启用：草稿 → active（此后才可用于生成）。 */
    @PostMapping(value = "/api/templates/{id}/activate", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode activate(@PathVariable long id) {
        Store.TemplateRow t = requireTemplate(id);
        if (t.builtin()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "内置模板无需启用");
        }
        if (!"active".equals(t.status())) {
            store.activateTemplate(id);
            log.info("template activated id={} name={}", id, t.name());
        }
        return TemplateMapper.toJson(requireTemplate(id));
    }

    // ---------------- 新建 ----------------
    @PostMapping(value = "/api/templates", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> create(@Valid @RequestBody TemplateBody body) {
        long id = store.insertTemplate(
                body.kind() == null || body.kind().isBlank() ? "content" : body.kind(),
                body.name().trim(),
                body.voice(), body.opening(),
                writeArray(body.structure()), body.closing(),
                body.tag_style(), writeArray(body.taboo()),
                false, null, 1);
        log.info("template created id={} name={}", id, body.name());
        Store.TemplateRow t = store.getTemplate(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "create failed"));
        return ResponseEntity.status(HttpStatus.CREATED).body(TemplateMapper.toJson(t));
    }

    // ---------------- 详情 ----------------
    @GetMapping(value = "/api/templates/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode get(@PathVariable long id) {
        Store.TemplateRow t = requireTemplate(id);
        return TemplateMapper.toJson(t);
    }

    // ---------------- 编辑（version+1） ----------------
    @PutMapping(value = "/api/templates/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode update(@PathVariable long id, @Valid @RequestBody TemplateBody body) {
        requireTemplate(id);
        boolean ok = store.updateTemplate(id, body.name().trim(), body.voice(), body.opening(),
                writeArray(body.structure()), body.closing(), body.tag_style(), writeArray(body.taboo()));
        if (!ok) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "template not found");
        }
        Store.TemplateRow t = requireTemplate(id);
        log.info("template updated id={} name={} -> v{}", id, t.name(), t.version());
        return TemplateMapper.toJson(t);
    }

    // ---------------- 删除（builtin 拒绝） ----------------
    @DeleteMapping(value = "/api/templates/{id}")
    public ResponseEntity<Void> delete(@PathVariable long id) {
        Store.TemplateRow t = requireTemplate(id);
        if (t.builtin()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "内置模板不可删除，请使用「复制为我的」");
        }
        store.deleteTemplate(id);
        log.info("template deleted id={} name={}", id, t.name());
        return ResponseEntity.noContent().build();
    }

    // ---------------- 复制为我的 ----------------
    @PostMapping(value = "/api/templates/{id}/duplicate", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> duplicate(@PathVariable long id) {
        Store.TemplateRow src = requireTemplate(id);
        String newName = uniqueName(src.name() + "（副本）");
        long nid = store.insertTemplate(src.kind(), newName, src.voice(), src.opening(),
                src.structureJson(), src.closing(), src.tagStyle(), src.tabooJson(),
                false, src.id(), 1);
        Store.TemplateRow t = requireTemplate(nid);
        log.info("template duplicated src={} -> id={} name={}", id, nid, newName);
        return ResponseEntity.status(HttpStatus.CREATED).body(TemplateMapper.toJson(t));
    }

    // ---------------- 导出 ----------------
    /**
     * 导出**已启用**模板（用户资产视角：草稿是待确认的中间态，不纳入备份）。
     * 条目不含本机标识（id/builtin/origin_id），便于跨机器共享。
     */
    @GetMapping(value = "/api/templates/export", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode export() {
        ObjectNode out = mapper.createObjectNode();
        out.put("polyface_templates", 1);
        out.put("exported_at", LocalDateTime.now().toString());
        ArrayNode arr = out.putArray("templates");
        for (Store.TemplateRow t : store.listTemplates(null, "active")) {
            arr.add(TemplateMapper.toExportJson(t));
        }
        return out;
    }

    // ---------------- 导入（冲突交用户判断） ----------------
    @PostMapping(value = "/api/templates/import", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> importTemplates(@RequestBody JsonNode body) {
        if (body == null || !body.has("polyface_templates")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid template file");
        }
        if (body.path("polyface_templates").asInt(0) != 1) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported template file version");
        }
        JsonNode list = body.path("templates");
        if (!list.isArray() || list.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid template file");
        }

        // 1) 收集冲突（不自动决策）
        List<JsonNode> incoming = new ArrayList<>();
        List<Store.TemplateRow> conflicts = new ArrayList<>();
        for (JsonNode t : list) {
            String name = t.path("name").asText("").trim();
            if (name.isEmpty()) {
                continue;
            }
            incoming.add(t);
            store.findTemplateByName(name).ifPresent(conflicts::add);
        }

        JsonNode ocNode = body.get("on_conflict");
        String onConflict = (ocNode == null || ocNode.isNull()) ? "" : ocNode.asText("").trim();
        boolean hasConflict = !conflicts.isEmpty();
        if (hasConflict && onConflict.isEmpty()) {
            ObjectNode out = mapper.createObjectNode();
            out.put("detail", "存在同名模板，请选择处理方式");
            ArrayNode arr = out.putArray("conflicts");
            for (Store.TemplateRow c : conflicts) {
                ObjectNode n = arr.addObject();
                n.put("name", c.name());
                n.put("existing_id", c.id());
                n.put("existing_builtin", c.builtin());
            }
            return ResponseEntity.status(HttpStatus.CONFLICT).body(out);
        }
        if (hasConflict && !List.of("skip", "overwrite", "keep_both").contains(onConflict)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "on_conflict 仅支持 skip / overwrite / keep_both");
        }

        // 2) 按策略落库
        int imported = 0, skipped = 0, overwritten = 0, keptBoth = 0;
        for (JsonNode t : incoming) {
            String name = t.path("name").asText("").trim();
            Optional<Store.TemplateRow> existing = store.findTemplateByName(name);
            if (existing.isPresent()) {
                Store.TemplateRow ex = existing.get();
                switch (onConflict) {
                    case "skip" -> {
                        skipped++;
                        continue;
                    }
                    case "overwrite" -> {
                        // 内置模板不可被覆盖（红线：内置库只读）→ 降级为并存
                        if (!ex.builtin()) {
                            store.overwriteTemplateByName(name,
                                    t.path("voice").asText(""), t.path("opening").asText(""),
                                    t.path("structure").toString(), t.path("closing").asText(""),
                                    t.path("tag_style").asText(""), t.path("taboo").toString());
                            overwritten++;
                            continue;
                        }
                        insertImported(t, uniqueName(name + "(导入)"));
                        keptBoth++;
                        continue;
                    }
                    default -> { // keep_both
                        insertImported(t, uniqueName(name + "(导入)"));
                        keptBoth++;
                        continue;
                    }
                }
            }
            insertImported(t, name);
            imported++;
        }

        ObjectNode out = mapper.createObjectNode();
        out.put("imported", imported);
        out.put("skipped", skipped);
        out.put("overwritten", overwritten);
        out.put("kept_both", keptBoth);
        ArrayNode arr = out.putArray("templates");
        for (Store.TemplateRow t : store.listTemplates(null)) {
            arr.add(TemplateMapper.toJson(t));
        }
        log.info("templates imported: new={} skipped={} overwritten={} keptBoth={}",
                imported, skipped, overwritten, keptBoth);
        return ResponseEntity.ok(out);
    }

    // ---------------- helpers ----------------
    private void insertImported(JsonNode t, String name) {
        store.insertTemplate(
                t.path("kind").asText("content"),
                name,
                t.path("voice").asText(""),
                t.path("opening").asText(""),
                t.path("structure").toString(),
                t.path("closing").asText(""),
                t.path("tag_style").asText(""),
                t.path("taboo").toString(),
                false, null, 1);
    }

    private Store.TemplateRow requireTemplate(long id) {
        return store.getTemplate(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "template not found"));
    }

    /** 名称去重：base / base(2) / base(3) … */
    private String uniqueName(String base) {
        if (store.findTemplateByName(base).isEmpty()) {
            return base;
        }
        for (int i = 2; i < 1000; i++) {
            String cand = base + "(" + i + ")";
            if (store.findTemplateByName(cand).isEmpty()) {
                return cand;
            }
        }
        return base + "(" + System.currentTimeMillis() + ")";
    }

    private String writeArray(List<String> items) {
        if (items == null || items.isEmpty()) {
            return "[]";
        }
        ArrayNode arr = mapper.createArrayNode();
        for (String s : items) {
            if (s != null && !s.isBlank()) {
                arr.add(s.trim());
            }
        }
        return arr.toString();
    }
}
