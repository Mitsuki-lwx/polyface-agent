package com.polyface.backend.job;

import java.util.ArrayList;
import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 生成稿件的**共用逻辑**（M8 第二片）。
 *
 * <p>为什么抽出来：同步端点（`/api/materials/{id}/generate`）与异步任务（job）走的是**同一件事** ——
 * 组装 Python 请求体、调 Python、把草稿落库。抄成两份必然漂移，这正是本项目反复吃亏的地方。
 *
 * <p>本类只做这三件事，**不管编排**：谁先谁后、按平台拆还是一次多平台，由调用方决定。
 */
@Component
public class GenerateService {

    private static final Logger log = LoggerFactory.getLogger(GenerateService.class);

    private final Store store;
    private final PythonClient python;
    private final ObjectMapper mapper = new ObjectMapper();

    /** 一次最多引用几条历史素材（spec：不许把整库传过去 ✗）。 */
    static final int MAX_LIBRARY_REFS = 3;

    public GenerateService(Store store, PythonClient python) {
        this.store = store;
        this.python = python;
    }

    /**
     * 组装 Python `/generate` 的请求体。
     *
     * @param eventsPath 阶段事件出口（可选）。给了 Python 会把「理解/每平台 策略→成稿→质检」
     *                   写成 JSONL，Java 侧靠它算进度（见 `docs/spec_async_generate.md` §5）
     */
    public ObjectNode buildBody(Store.MaterialRow m, List<String> platforms,
                                JsonNode template, String toneOverride, String eventsPath,
                                boolean useLibrary) {
        ObjectNode body = mapper.createObjectNode();
        body.put("raw_text", m.rawText());
        body.put("source_kind", m.sourceKind() == null ? "general" : m.sourceKind());
        if (m.title() != null) {
            body.put("title", m.title());
        }
        ArrayNode arr = body.putArray("platforms");
        platforms.forEach(arr::add);
        if (toneOverride != null) {
            body.put("tone_override", toneOverride);
        }
        if (template != null && !template.isNull()) {
            body.set("template", template);
        }
        // 创作者画像（FR-32→FR-33）
        store.getProfile().ifPresent(p -> {
            ObjectNode pn = mapper.createObjectNode();
            pn.put("brand_voice", p.brandVoice());
            pn.put("domain", p.domain());
            pn.put("audience", p.audience());
            pn.put("avoid", p.avoid());
            body.set("creator_profile", pn);
        });
        // 复盘建议（FR-31→FR-33）
        var hints = store.listRetros(10);
        if (!hints.isEmpty()) {
            ArrayNode hArr = body.putArray("retrospect_hints");
            for (Store.RetroRow r : hints) {
                hArr.add("[" + r.platformCode() + "] " + r.insight());
            }
        }
        // 事实确认闭环（FR-34）：已确认的事实是**生成唯一依据**，Python 侧据此跳过重复理解。
        // 这条也是"按平台拆调用"仍然只理解一次的原因（docs/spec_async_generate.md §5）。
        ObjectNode cf = confirmedFacts(m);
        if (cf != null) {
            body.set("confirmed_facts", cf);
            log.debug("material {} 使用已确认事实，跳过重复理解", m.id());
        }
        if (eventsPath != null && !eventsPath.isBlank()) {
            body.put("events_path", eventsPath);
        }
        // 图片素材（image-as-material-source）：把原图路径交给 Python，
        // 它会在生成前做一次单独观察 ✓。**不传**时字段不出现 ✓。
        if (m.imagePath() != null && !m.imagePath().isBlank()) {
            // 同一个字段既可能存图片也可能存视频（素材导入时按扩展名判断 ✓）。
            // 视频走**抽帧观察**、图片走**整图观察**，是两个不同的提示词路径 ✗ 不要混。
            String lower = m.imagePath().toLowerCase();
            if (lower.endsWith(".mp4") || lower.endsWith(".mov") || lower.endsWith(".mkv")
                    || lower.endsWith(".webm") || lower.endsWith(".m4v")) {
                body.put("source_video", m.imagePath());
            } else {
                body.put("source_image", m.imagePath());
            }
        }
        // 关联本地素材库（openspec: library-context-generation）
        // 只在**勾选时**出现该字段 —— 未勾选时请求体与改动前逐字段一致 ✓。
        if (useLibrary) {
            ArrayNode refs = body.putArray("library_context");
            for (Store.MaterialRow r : related(m)) {
                ObjectNode n = mapper.createObjectNode();
                n.put("id", r.id());
                n.put("title", snippet(r.title() == null ? "" : r.title(), 40));
                n.put("snippet", snippet(r.rawText(), 200));
                try {
                    n.set("keywords", mapper.readTree(r.tagsJson() == null ? "[]" : r.tagsJson()));
                } catch (Exception e) {
                    n.putArray("keywords");
                }
                refs.add(n);
            }
            log.info("material {} 关联素材库：引用 {} 条", m.id(), refs.size());
        }
        return body;
    }

    /** 最多 3 条（spec：只引用检索到的少数几条）。 */
    private java.util.List<Store.MaterialRow> related(Store.MaterialRow m) {
        java.util.List<String> tags = new java.util.ArrayList<>();
        try {
            JsonNode arr = mapper.readTree(m.tagsJson() == null ? "[]" : m.tagsJson());
            if (arr.isArray()) {
                arr.forEach(t -> tags.add(t.asText()));
            }
        } catch (Exception e) {
            log.debug("tags_json 解析失败，跳过素材库关联：{}", e.getMessage());
        }
        return store.relatedMaterials(m.id(), tags, MAX_LIBRARY_REFS);
    }

    private static String snippet(String s, int max) {
        if (s == null) {
            return "";
        }
        String t = s.replaceAll("\\s+", " ").trim();
        return t.length() <= max ? t : t.substring(0, max) + "…";
    }

    /** 已确认事实 → StructuredMaterial 形状；没确认或为空则返回 null（由 Python 重新理解）。 */
    public ObjectNode confirmedFacts(Store.MaterialRow m) {
        if (!m.factsConfirmed() || m.factsJson() == null || m.factsJson().isBlank()) {
            return null;
        }
        try {
            JsonNode factsArr = mapper.readTree(m.factsJson());
            if (factsArr == null || !factsArr.isArray() || factsArr.isEmpty()) {
                return null;
            }
            ObjectNode cf = mapper.createObjectNode();
            cf.put("core_message", m.coreMessage() == null ? "" : m.coreMessage());
            cf.put("tone", m.tone() == null ? "" : m.tone());
            cf.put("audience", m.audience() == null ? "" : m.audience());
            cf.set("facts", factsArr);
            return cf;
        } catch (Exception e) {
            log.warn("facts_json 解析失败，回退为重新理解：{}", e.getMessage());
            return null;
        }
    }

    public JsonNode callPython(ObjectNode body) {
        return python.postGenerate(body);
    }

    /** 单篇草稿的落库结果，供任务详情回显"生成了哪几篇"。 */
    public record StoredDraft(long id, String platformCode, String platformName, String status) {
    }

    /** 把 Python 返回的草稿落库（**唯一一份**落库逻辑）。 */
    public List<StoredDraft> storeDrafts(long materialId, JsonNode pyDrafts,
                                         Long templateId, Integer templateVersion) {
        List<StoredDraft> out = new ArrayList<>();
        for (JsonNode pd : pyDrafts) {
            String code = pd.path("platform_code").asText("?");
            String name = pd.path("platform_name").asText(code);
            boolean passed = pd.path("qa").path("passed").asBoolean(false);
            String status = passed ? "qa_passed" : "needs_review";
            long draftId = store.insertDraft(materialId, code, name,
                    pd.path("brief").toString(), pd.path("draft").toString(),
                    pd.path("qa").toString(), status, templateId, templateVersion);
            out.add(new StoredDraft(draftId, code, name, status));
        }
        return out;
    }

    public ObjectMapper mapper() {
        return mapper;
    }
}
