package com.polyface.backend.web;

import java.util.List;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
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
import jakarta.validation.constraints.NotEmpty;

/** 素材与成稿的本地业务 API：入库 / 生成 / 查询（数据全部落 SQLite）。 */
@RestController
public class MaterialController {

    private static final Logger log = LoggerFactory.getLogger(MaterialController.class);
    private final PythonClient python;
    private final Store store;
    private final ObjectMapper mapper = new ObjectMapper();

    public MaterialController(PythonClient python, Store store) {
        this.python = python;
        this.store = store;
    }

    // ---------------- DTO ----------------
    public record CreateMaterialRequest(
            @NotBlank String raw_text, String source_kind, String title) {
    }

    public record GenerateRequest(
            @NotEmpty List<String> platforms, String tone_override,
            Long template_id,
            com.fasterxml.jackson.databind.JsonNode template) {
    }

    @GetMapping(value = "/api/platforms", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode platforms() {
        return python.platforms();
    }

    // ---------------- 素材 ----------------
    @PostMapping(value = "/api/materials", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> createMaterial(@Valid @RequestBody CreateMaterialRequest req) {
        try {
            JsonNode analyzeResp = python.analyze(req.raw_text(), req.source_kind(), req.title());
            JsonNode structured = analyzeResp.path("structured");
            boolean mock = analyzeResp.path("used_mock").asBoolean(false);

            long id = store.insertMaterial(
                    req.raw_text(),
                    req.source_kind() == null ? "general" : req.source_kind(),
                    req.title(),
                    structured.path("core_message").asText(""),
                    structured.path("tone").asText(""),
                    structured.path("audience").asText(""),
                    structured.path("facts").toString());

            ObjectNode out = mapper.createObjectNode();
            out.put("id", id);
            out.put("raw_text", req.raw_text());
            out.put("source_kind", req.source_kind());
            if (req.title() != null) {
                out.put("title", req.title());
            }
            out.set("structured", structured);
            out.put("used_mock", mock);
            log.info("material created id={}", id);
            return ResponseEntity.ok(out);
        } catch (Exception ex) {
            log.error("createMaterial failed", ex);
            throw new GlobalExceptionHandler.LlmUnavailableException(ex.getMessage());
        }
    }

    @GetMapping(value = "/api/materials", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode listMaterials() {
        ArrayNode out = mapper.createArrayNode();
        for (Store.MaterialRow m : store.listMaterials()) {
            ObjectNode n = mapper.createObjectNode();
            n.put("id", m.id());
            n.put("core_message", m.coreMessage());
            n.put("source_kind", m.sourceKind());
            if (m.title() != null) {
                n.put("title", m.title());
            }
            n.put("created_at", m.createdAt());
            n.put("draft_count", store.draftsByMaterial(m.id()).size());
            out.add(n);
        }
        return out;
    }

    @GetMapping(value = "/api/materials/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode getMaterial(@PathVariable long id) {
        Store.MaterialRow m = store.getMaterial(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "material not found"));
        ObjectNode out = mapper.createObjectNode();
        out.put("id", m.id());
        out.put("raw_text", m.rawText());
        out.put("source_kind", m.sourceKind());
        if (m.title() != null) {
            out.put("title", m.title());
        }
        out.put("core_message", m.coreMessage());
        out.put("tone", m.tone());
        out.put("audience", m.audience());
        try {
            out.set("facts", mapper.readTree(m.factsJson()));
        } catch (Exception ignore) {
            out.putArray("facts");
        }
        out.set("drafts", draftsJson(m.id()));
        return out;
    }

    // ---------------- 成稿生成 ----------------
    @PostMapping(value = "/api/materials/{id}/generate", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> generate(@PathVariable long id,
                                             @Valid @RequestBody GenerateRequest req) {
        Store.MaterialRow m = store.getMaterial(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "material not found"));

        // FR-64：template_id 优先于内联 template；模板来源随稿存档（id + version）
        JsonNode effectiveTemplate = req.template();
        Long tplId = null;
        Integer tplVer = null;
        if (req.template_id() != null) {
            Store.TemplateRow t = store.getTemplate(req.template_id())
                    .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "template not found"));
            effectiveTemplate = TemplateMapper.toUserTemplate(t);
            tplId = t.id();
            tplVer = t.version();
        }

        try {
            // FR-33 复盘回写：自动拉画像 + 复盘建议，注入 Python 提示词
            com.fasterxml.jackson.databind.node.ObjectNode pyBody = mapper.createObjectNode();
            pyBody.put("raw_text", m.rawText());
            pyBody.put("source_kind", m.sourceKind() == null ? "general" : m.sourceKind());
            if (m.title() != null) pyBody.put("title", m.title());
            var arr = pyBody.putArray("platforms");
            req.platforms().forEach(arr::add);
            if (req.tone_override() != null) pyBody.put("tone_override", req.tone_override());
            if (effectiveTemplate != null && !effectiveTemplate.isNull()) {
                pyBody.set("template", effectiveTemplate);
            }
            // 创作者画像（FR-32→FR-33）
            store.getProfile().ifPresent(p -> {
                com.fasterxml.jackson.databind.node.ObjectNode pn = mapper.createObjectNode();
                pn.put("brand_voice", p.brandVoice());
                pn.put("domain", p.domain());
                pn.put("audience", p.audience());
                pn.put("avoid", p.avoid());
                pyBody.set("creator_profile", pn);
            });
            // 复盘建议（FR-31→FR-33）：从数据库读最近 10 条 retro
            var hints = store.listRetros(10);
            if (!hints.isEmpty()) {
                var hArr = pyBody.putArray("retrospect_hints");
                for (Store.RetroRow r : hints) {
                    hArr.add("[" + r.platformCode() + "] " + r.insight());
                }
            }
            JsonNode genResp = python.postGenerate(pyBody);
            boolean mock = genResp.path("used_mock").asBoolean(false);

            ObjectNode out = mapper.createObjectNode();
            out.put("material_id", id);
            out.put("used_mock", mock);
            if (tplId != null) {
                out.put("template_id", tplId);
                out.put("template_version", tplVer);
            }
            ArrayNode draftsOut = out.putArray("drafts");

            JsonNode pyDrafts = genResp.path("drafts");
            for (JsonNode pd : pyDrafts) {
                String code = pd.path("platform_code").asText("?");
                String name = pd.path("platform_name").asText(code);
                boolean passed = pd.path("qa").path("passed").asBoolean(false);
                String status = passed ? "qa_passed" : "needs_review";

                long draftId = store.insertDraft(
                        id, code, name,
                        pd.path("brief").toString(),
                        pd.path("draft").toString(),
                        pd.path("qa").toString(),
                        status, tplId, tplVer);

                ObjectNode d = draftsOut.addObject();
                d.put("id", draftId);
                d.put("material_id", id);
                d.put("platform_code", code);
                d.put("platform_name", name);
                d.put("status", status);
                if (tplId != null) {
                    d.put("template_id", tplId);
                    d.put("template_version", tplVer);
                }
                d.set("brief", pd.path("brief"));
                d.set("draft", pd.path("draft"));
                d.set("qa", pd.path("qa"));
            }
            log.info("material {} generated {} drafts (template={} v{})",
                    id, pyDrafts.size(), tplId, tplVer);
            return ResponseEntity.ok(out);
        } catch (Exception ex) {
            log.error("generate failed", ex);
            throw new GlobalExceptionHandler.LlmUnavailableException(ex.getMessage());
        }
    }

    // ---------------- 单稿 ----------------
    @GetMapping(value = "/api/drafts/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode getDraft(@PathVariable long id) {
        Store.DraftRow d = store.getDraft(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "draft not found"));
        ObjectNode out = mapper.createObjectNode();
        out.put("id", d.id());
        out.put("material_id", d.materialId());
        out.put("platform_code", d.platformCode());
        out.put("platform_name", d.platformName());
        out.put("status", d.status());
        out.put("created_at", d.createdAt());
        if (d.templateId() != null) {
            out.put("template_id", d.templateId());
            out.put("template_version", d.templateVersion());
        }
        try {
            out.set("brief", mapper.readTree(d.briefJson()));
            out.set("draft", mapper.readTree(d.payloadJson()));
            out.set("qa", mapper.readTree(d.qaJson()));
        } catch (Exception e) {
            throw new RuntimeException("draft json corrupt id=" + id, e);
        }
        return out;
    }

    private ArrayNode draftsJson(long materialId) {
        ArrayNode arr = mapper.createArrayNode();
        for (Store.DraftRow d : store.draftsByMaterial(materialId)) {
            ObjectNode n = arr.addObject();
            n.put("id", d.id());
            n.put("platform_code", d.platformCode());
            n.put("platform_name", d.platformName());
            n.put("status", d.status());
            n.put("created_at", d.createdAt());
            if (d.templateId() != null) {
                n.put("template_id", d.templateId());
                n.put("template_version", d.templateVersion());
            }
        }
        return arr;
    }
}
