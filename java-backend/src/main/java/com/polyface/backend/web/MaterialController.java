package com.polyface.backend.web;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
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
import jakarta.validation.constraints.NotEmpty;

/** 素材与成稿的本地业务 API：入库 / 生成 / 查询（数据全部落 SQLite）。 */
@RestController
public class MaterialController {

    private static final Logger log = LoggerFactory.getLogger(MaterialController.class);
    private final PythonClient python;
    private final Store store;
    private final com.polyface.backend.observability.LangfuseReporter langfuse;
    private final ObjectMapper mapper = new ObjectMapper();

    public MaterialController(PythonClient python, Store store,
                              com.polyface.backend.observability.LangfuseReporter langfuse) {
        this.python = python;
        this.store = store;
        this.langfuse = langfuse;
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
            // 事实确认闭环（FR-34）：新素材默认未确认
            out.put("facts_confirmed", false);
            out.putNull("facts_confirmed_at");
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
        // 事实确认状态（FR-34）：前端据此决定是否提示"确认后可少一次 AI 调用"
        out.put("facts_confirmed", m.factsConfirmed());
        if (m.factsConfirmedAt() != null) {
            out.put("facts_confirmed_at", m.factsConfirmedAt());
        }
        // 供前端重新渲染解析区（含可编辑事实清单）
        ObjectNode structured = mapper.createObjectNode();
        structured.put("core_message", m.coreMessage() == null ? "" : m.coreMessage());
        structured.put("tone", m.tone() == null ? "" : m.tone());
        structured.put("audience", m.audience() == null ? "" : m.audience());
        structured.set("facts", out.path("facts"));
        out.set("structured", structured);
        out.set("drafts", draftsJson(m.id()));
        return out;
    }

    // ---------------- 成稿生成 ----------------
    @PostMapping(value = "/api/materials/{id}/generate", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> generate(@PathVariable long id,
                                             @Valid @RequestBody GenerateRequest req) {
        // 链路标识（FR-71）：由 TraceFilter 生成；Python 侧沿用同一 id
        String traceId = com.polyface.backend.observability.TraceContext.getOrCreate();
        long startedAt = System.currentTimeMillis();
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
            // 事实确认闭环（FR-34）：已确认的事实作为生成唯一依据，Python 侧跳过重复理解
            // 注：facts_json 存的是**数组**，此处按 StructuredMaterial 结构包装
            if (m.factsConfirmed() && m.factsJson() != null && !m.factsJson().isBlank()) {
                try {
                    JsonNode factsArr = mapper.readTree(m.factsJson());
                    if (factsArr != null && factsArr.isArray() && !factsArr.isEmpty()) {
                        ObjectNode cf = mapper.createObjectNode();
                        cf.put("core_message", m.coreMessage() == null ? "" : m.coreMessage());
                        cf.put("tone", m.tone() == null ? "" : m.tone());
                        cf.put("audience", m.audience() == null ? "" : m.audience());
                        cf.set("facts", factsArr);
                        pyBody.set("confirmed_facts", cf);
                        log.info("material {} 使用已确认事实，将跳过重复理解", id);
                    }
                } catch (Exception e) {
                    log.warn("facts_json 解析失败，回退为重新理解：{}", e.getMessage());
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
            long elapsed = System.currentTimeMillis() - startedAt;
            // 部分失败可容忍（FR-42）：Python 侧已逐平台容错，这里只透传与计数，
            // 成功的稿子照常落库，**不因个别平台失败而整体 502**
            JsonNode pyFailures = genResp.path("failures");
            int failCount = (pyFailures.isArray()) ? pyFailures.size() : 0;
            out.put("ok_count", draftsOut.size());
            out.put("fail_count", failCount);
            if (failCount > 0) {
                out.set("failures", pyFailures);
                log.warn("material {} 部分平台失败（成功 {} / 失败 {}）：{}",
                        id, draftsOut.size(), failCount, pyFailures);
            }
            log.info("material {} generated {} drafts, {} failures (template={} v{}) trace={} elapsed={}ms",
                    id, pyDrafts.size(), failCount, tplId, tplVer, traceId, elapsed);
            // 观测：Java 编排阶段耗时（与 Python 侧 LLM 调用归入同一 trace）
            langfuse.reportSpan(traceId, "java.orchestrate.generate", elapsed);
            out.put("trace_id", traceId);
            return ResponseEntity.ok(out);
        } catch (org.springframework.web.client.ResourceAccessException ex) {
            // 超时/连接中断：Spring 会包装为 ResourceAccessException（其中可能含 SocketTimeoutException）。
            // 属长任务预算问题：**不谎报成功**，明确告知未完成并可重试
            long elapsed = System.currentTimeMillis() - startedAt;
            boolean timedOut = ex.getCause() instanceof java.net.SocketTimeoutException
                    || String.valueOf(ex.getMessage()).toLowerCase().contains("timed out");
            log.error("generate {} trace={} elapsed={}ms（可用 POLYFACE_LLM_TIMEOUT_SEC 调整预算）：{}",
                    timedOut ? "timeout" : "connect-error", traceId, elapsed, ex.getMessage());
            throw new GlobalExceptionHandler.LlmUnavailableException(
                    (timedOut ? "生成超时" : "与生成服务连接中断")
                            + "（已等待 " + (elapsed / 1000) + "s）：上游限流时单平台可能需数分钟。"
                            + "请稍后重试，或减少平台数量后重试。");
        } catch (org.springframework.web.client.HttpClientErrorException ex) {
            // Python 侧的业务校验失败（如平台代码非法）应**原样透传 4xx**，
            // 而不是被包装成 502 —— 502 表示"上游不可用"，会让用户误以为服务坏了
            if (ex.getStatusCode().is4xxClientError()) {
                String body = ex.getResponseBodyAsString();
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                        (body == null || body.isBlank()) ? ex.getMessage() : body);
            }
            throw new GlobalExceptionHandler.LlmUnavailableException(ex.getMessage());
        } catch (Exception ex) {
            log.error("generate failed trace={}", traceId, ex);
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

    // ---------------- 事实确认（FR-34 防幻觉地基）----------------
    public record SaveFactsRequest(String core_message, String tone, String audience,
                                   List<Map<String, Object>> facts, Boolean confirm) {}

    /** 保存（并可选确认）事实清单。确认后该版本即成为后续生成的唯一事实依据。 */
    @PutMapping(value = "/api/materials/{id}/facts", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode saveFacts(@PathVariable long id, @RequestBody SaveFactsRequest req) {
        store.getMaterial(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "material not found"));
        List<Map<String, Object>> facts = req.facts() == null ? List.of() : req.facts();
        // 校验与 Python FactType 保持一致（data|story|opinion），
        // 提前拦截可避免把非法值传到下游才 422
        java.util.Set<String> allowedTypes = java.util.Set.of("data", "story", "opinion");
        for (int i = 0; i < facts.size(); i++) {
            Object t = facts.get(i).get("text");
            if (t == null || String.valueOf(t).isBlank()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                        "facts[" + i + "].text 不能为空");
            }
            Object ty = facts.get(i).get("type");
            String typeStr = ty == null ? "data" : String.valueOf(ty);
            if (!allowedTypes.contains(typeStr)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                        "facts[" + i + "].type 非法（仅允许 data|story|opinion）：" + typeStr);
            }
            facts.get(i).put("type", typeStr);
        }
        boolean confirm = Boolean.TRUE.equals(req.confirm());
        String factsJson;
        try {
            factsJson = mapper.writeValueAsString(facts);
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "facts 序列化失败");
        }
        store.saveFacts(id, factsJson, req.core_message(), req.tone(), req.audience(), confirm);
        ObjectNode out = mapper.createObjectNode();
        out.put("id", id);
        out.put("facts_confirmed", confirm);
        out.put("facts_count", facts.size());
        log.info("material {} facts saved: count={} confirmed={}", id, facts.size(), confirm);
        return out;
    }

    // ---------------- 稿件编辑与导出（FR-42）----------------
    public record EditDraftRequest(List<String> titles, String body, List<String> tags,
                                   String interaction_line, String cover_suggestion) {}

    /** 保存人工编辑后的稿件。
     *
     * <p>**只**更新可编辑字段（标题/正文/标签/互动句/封面建议），
     * qa / brief / clip_sheet / rationale 不经此路径，避免绕过质检。
     */
    @PutMapping(value = "/api/drafts/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode editDraft(@PathVariable long id, @RequestBody EditDraftRequest req) {
        Store.DraftRow d = store.getDraft(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "draft not found"));
        ObjectNode payload;
        try {
            payload = (ObjectNode) mapper.readTree(d.payloadJson());
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "稿件数据损坏");
        }
        // 白名单更新
        if (req.titles() != null) {
            ArrayNode ta = mapper.createArrayNode();
            req.titles().forEach(ta::add);
            payload.set("titles", ta);
        }
        if (req.body() != null) payload.put("body", req.body());
        if (req.tags() != null) {
            ArrayNode ga = mapper.createArrayNode();
            req.tags().forEach(ga::add);
            payload.set("tags", ga);
        }
        if (req.interaction_line() != null) payload.put("interaction_line", req.interaction_line());
        if (req.cover_suggestion() != null) payload.put("cover_suggestion", req.cover_suggestion());

        String json;
        try {
            json = mapper.writeValueAsString(payload);
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "稿件序列化失败");
        }
        store.updateDraftPayload(id, json);
        log.info("draft {} edited by human", id);

        ObjectNode out = mapper.createObjectNode();
        out.put("id", id);
        out.put("edited_at", java.time.LocalDateTime.now().toString());
        out.set("draft", payload);
        out.set("qa", safeTree(d.qaJson()));
        return out;
    }

    /** 导出稿件：format=md（默认，含标签与互动句）| txt（仅标题+正文）。 */
    @GetMapping(value = "/api/drafts/{id}/export")
    public ResponseEntity<String> exportDraft(@PathVariable long id,
                                              @RequestParam(defaultValue = "md") String format) {
        Store.DraftRow d = store.getDraft(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "draft not found"));
        JsonNode p = safeTree(d.payloadJson());
        List<String> titles = new java.util.ArrayList<>();
        if (p.path("titles").isArray()) p.path("titles").forEach(t -> titles.add(t.asText()));
        List<String> tags = new java.util.ArrayList<>();
        if (p.path("tags").isArray()) p.path("tags").forEach(t -> tags.add(t.asText()));
        String body = p.path("body").asText("");
        String interaction = p.path("interaction_line").asText("");

        String filename = d.platformCode() + "-material" + d.materialId() + "-draft" + id;
        String content = "txt".equalsIgnoreCase(format)
                ? (titles.isEmpty() ? "" : titles.get(0) + "\n\n") + body
                : buildMarkdown(titles, body, tags, interaction);
        String ext = "txt".equalsIgnoreCase(format) ? "txt" : "md";
        String encoded = java.net.URLEncoder.encode(filename + "." + ext, java.nio.charset.StandardCharsets.UTF_8)
                .replace("+", "%20");
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType("text/plain; charset=utf-8"))
                .header("Content-Disposition", "attachment; filename*=UTF-8''" + encoded)
                .body(content);
    }

    private String buildMarkdown(List<String> titles, String body, List<String> tags, String interaction) {
        StringBuilder sb = new StringBuilder();
        if (!titles.isEmpty()) {
            sb.append("# ").append(titles.get(0)).append("\n\n");
            for (int i = 1; i < titles.size(); i++) {
                sb.append("- 备选标题：").append(titles.get(i)).append("\n");
            }
            sb.append("\n");
        }
        sb.append(body).append("\n");
        if (!tags.isEmpty()) {
            sb.append("\n**标签**：").append(String.join(" ", tags.stream().map(t -> "#" + t).toList())).append("\n");
        }
        if (interaction != null && !interaction.isBlank()) {
            sb.append("\n**互动引导**：").append(interaction).append("\n");
        }
        return sb.toString();
    }

    private JsonNode safeTree(String json) {
        try {
            return json == null ? mapper.createObjectNode() : mapper.readTree(json);
        } catch (Exception e) {
            return mapper.createObjectNode();
        }
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
