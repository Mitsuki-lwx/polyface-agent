package com.polyface.backend.web;

import java.util.ArrayList;
import java.util.LinkedHashMap;
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
import com.polyface.backend.store.Store;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;

/**
 * 效果回填 / 复盘 / 创作者画像三个本地业务 API（FR-30/31/32/33）。
 * 全部走本地 SQLite；复盘洞察由 Java 聚合 rules 生成（不调 LLM，离线可用）。
 */
@RestController
public class EffectController {

    private static final Logger log = LoggerFactory.getLogger(EffectController.class);
    private final Store store;
    private final ObjectMapper mapper = new ObjectMapper();

    public EffectController(Store store) {
        this.store = store;
    }

    // ---------------- DTO ----------------
    public record EffectRequest(
            @NotNull @Min(0) Integer views,
            @NotNull @Min(0) Integer likes,
            @NotNull @Min(0) Integer favs,
            @NotNull @Min(0) Integer comments,
            String posted_at, String note) {
    }

    public record ProfileUpsertRequest(
            String brand_voice, String domain, String audience, String avoid) {
    }

    // ---------------- FR-30 效果回填 ----------------
    @PostMapping(value = "/api/drafts/{id}/effect", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<JsonNode> recordEffect(@PathVariable long id,
                                                  @RequestBody EffectRequest req) {
        // 校验草稿存在
        store.getDraft(id).orElseThrow(
                () -> new ResponseStatusException(HttpStatus.NOT_FOUND, "draft not found"));
        long eid = store.insertEffect(id,
                req.views(), req.likes(), req.favs(), req.comments(),
                req.posted_at(), req.note());
        ObjectNode out = mapper.createObjectNode();
        out.put("id", eid);
        out.put("draft_id", id);
        out.put("views", req.views());
        out.put("likes", req.likes());
        out.put("favs", req.favs());
        out.put("comments", req.comments());
        log.info("draft {} effect recorded: views={} likes={} favs={} comments={}",
                id, req.views(), req.likes(), req.favs(), req.comments());
        return ResponseEntity.ok(out);
    }

    /** 某稿的全部效果记录（倒序）。 */
    @GetMapping(value = "/api/drafts/{id}/effects", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode listDraftEffects(@PathVariable long id) {
        ArrayNode arr = mapper.createArrayNode();
        for (Store.EffectRow e : store.effectsByDraft(id)) {
            arr.add(effectToJson(e));
        }
        return arr;
    }

    /** 全部效果（聚合查询用）。 */
    @GetMapping(value = "/api/effects", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode listAllEffects(@RequestParam(required = false) Long draft_id) {
        ArrayNode arr = mapper.createArrayNode();
        List<Store.EffectRow> all = store.allEffects();
        for (Store.EffectRow e : all) {
            if (draft_id != null && e.draftId() != draft_id) continue;
            arr.add(effectToJson(e));
        }
        return arr;
    }

    private ObjectNode effectToJson(Store.EffectRow e) {
        ObjectNode n = mapper.createObjectNode();
        n.put("id", e.id());
        n.put("draft_id", e.draftId());
        n.put("views", e.views());
        n.put("likes", e.likes());
        n.put("favs", e.favs());
        n.put("comments", e.comments());
        if (e.postedAt() != null) n.put("posted_at", e.postedAt());
        if (e.note() != null) n.put("note", e.note());
        n.put("created_at", e.createdAt());
        return n;
    }

    // ---------------- FR-31 复盘洞察 ----------------
    /**
     * 聚合效果数据产出洞察报告：
     *   - 平台效果排行（avg views/likes）
     *   - 高表现稿件（top 3，按互动量=likes+comments*3+favs*2 排序）
     *   - 低表现稿件（bottom 3，给出避雷信号）
     *   - 复盘建议（基于聚合的启发式文案）
     * 数据不足(样本<3)时给引导语，不强出报告。
     */
    @GetMapping(value = "/api/retrospect", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode retrospect(@RequestParam(required = false) String platform,
                                @RequestParam(required = false) String domain) {
        // 收集 (draft, effect) 配对，每个 draft 取最新一条 effect
        // allEffects 按 (draft_id ASC, id ASC)；倒序遍历保留最后一条即最新
        List<Store.EffectRow> all = store.allEffects();
        Map<Long, Store.EffectRow> latestByDraft = new LinkedHashMap<>();
        for (int i = all.size() - 1; i >= 0; i--) {
            Store.EffectRow e = all.get(i);
            latestByDraft.putIfAbsent(e.draftId(), e);
        }

        // 拼接 draft + material + filter
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map.Entry<Long, Store.EffectRow> en : latestByDraft.entrySet()) {
            long did = en.getKey();
            Store.EffectRow eff = en.getValue();
            Optional<Store.DraftRow> drow = store.getDraft(did);
            if (drow.isEmpty()) continue;
            Store.DraftRow d = drow.get();
            Optional<Store.MaterialRow> mrow = store.getMaterial(d.materialId());
            if (mrow.isEmpty()) continue;
            Store.MaterialRow m = mrow.get();
            if (platform != null && !platform.isBlank() && !platform.equals(d.platformCode())) continue;
            if (domain != null && !domain.isBlank() && !domain.equals(m.audience())) continue;

            int engagement = eff.likes() + eff.comments() * 3 + eff.favs() * 2;
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("draft_id", did);
            r.put("platform_code", d.platformCode());
            r.put("platform_name", d.platformName());
            r.put("material_audience", m.audience());
            r.put("material_core", m.coreMessage());
            r.put("views", eff.views());
            r.put("likes", eff.likes());
            r.put("favs", eff.favs());
            r.put("comments", eff.comments());
            r.put("engagement", engagement);
            rows.add(r);
        }

        ObjectNode out = mapper.createObjectNode();
        out.put("sample_size", rows.size());
        if (rows.size() < 3) {
            out.put("ready", false);
            out.put("guidance", "样本不足（需≥3 条效果数据）。继续发布并录入效果，复盘会更准。");
            return out;
        }
        out.put("ready", true);

        // 平台排行
        Map<String, int[]> aggByPlat = new LinkedHashMap<>(); // code -> [n, views_sum, likes_sum, eng_sum]
        for (Map<String, Object> r : rows) {
            String pc = (String) r.get("platform_code");
            int[] a = aggByPlat.computeIfAbsent(pc, k -> new int[4]);
            a[0] += 1;
            a[1] += (int) r.get("views");
            a[2] += (int) r.get("likes");
            a[3] += (int) r.get("engagement");
        }
        ArrayNode rank = out.putArray("platform_ranking");
        List<Map.Entry<String, int[]>> platEntries = new ArrayList<>(aggByPlat.entrySet());
        platEntries.sort((x, y) -> Integer.compare(y.getValue()[3], x.getValue()[3]));
        for (Map.Entry<String, int[]> en : platEntries) {
            int[] a = en.getValue();
            ObjectNode p = rank.addObject();
            p.put("platform_code", en.getKey());
            p.put("count", a[0]);
            p.put("avg_views", a[0] == 0 ? 0 : (double) a[1] / a[0]);
            p.put("avg_likes", a[0] == 0 ? 0 : (double) a[2] / a[0]);
            p.put("avg_engagement", a[0] == 0 ? 0 : (double) a[3] / a[0]);
        }

        // top / bottom
        rows.sort((x, y) -> Integer.compare((int) y.get("engagement"), (int) x.get("engagement")));
        ArrayNode topArr = out.putArray("top_performers");
        for (int i = 0; i < Math.min(3, rows.size()); i++) {
            topArr.add(rowToJson(rows.get(i)));
        }
        ArrayNode botArr = out.putArray("underperformers");
        int from = Math.max(0, rows.size() - 3);
        for (int i = rows.size() - 1; i >= from; i--) {
            botArr.add(rowToJson(rows.get(i)));
        }

        // 复盘建议（启发式）
        ArrayNode insights = out.putArray("insights");
        if (!platEntries.isEmpty()) {
            Map.Entry<String, int[]> top = platEntries.get(0);
            double avgE = (double) top.getValue()[3] / Math.max(1, top.getValue()[0]);
            insights.addObject().put("kind", "good")
                    .put("insight", String.format("在[%s]平台的平均互动分最高(%.0f)，可优先在该平台深耕同款内容。",
                            top.getKey(), avgE));
        }
        if (rows.size() >= 6) {
            int topAvg = (int) rows.subList(0, 3).stream().mapToInt(r -> (int) r.get("engagement")).average().orElse(0);
            int botAvg = (int) rows.subList(rows.size() - 3, rows.size()).stream()
                    .mapToInt(r -> (int) r.get("engagement")).average().orElse(0);
            if (topAvg > 0 && botAvg > 0 && topAvg > botAvg * 2) {
                insights.addObject().put("kind", "bad")
                        .put("insight", String.format("Top3 平均互动 %d 是 Bottom3(%d) 的 %.1f 倍，差距来源值得对比拆解。",
                                topAvg, botAvg, (double) topAvg / Math.max(1, botAvg)));
            }
        }
        // 普适避雷
        insights.addObject().put("kind", "neutral")
                .put("insight", "复盘样本基于录入数据；持续录入 ≥10 条可观察更稳定的趋势。");

        // FR-31→FR-33 持久化：把本次聚合 insights 写入 retro_log（二次生成自动回写）
        persistInsights(insights);

        return out;
    }

    /** 按 (platform_code, insight) 简单去重后写入 retro_log，避免每次 GET 都灌一遍。 */
    private void persistInsights(ArrayNode insights) {
        // 已有 (platform_code, insight) 集合 → 跳过
        List<Store.RetroRow> existing = store.listRetros(200);
        java.util.Set<String> seen = new java.util.HashSet<>();
        for (Store.RetroRow r : existing) {
            seen.add(r.platformCode() + "||" + r.insight());
        }
        for (JsonNode i : insights) {
            String kind = i.path("kind").asText("neutral");
            String insight = i.path("insight").asText("");
            if (insight.isBlank()) continue;
            // insights 不带 platform_code，复盘整体属于"账号级"经验，platform_code 留空
            String key = "||" + insight;
            if (seen.contains(key)) continue;
            store.insertRetro(kind, null, null, insight);
            seen.add(key);
        }
    }

    private ObjectNode rowToJson(Map<String, Object> r) {
        ObjectNode n = mapper.createObjectNode();
        r.forEach((k, v) -> {
            if (v instanceof Number) n.putPOJO(k, v);
            else n.put(k, String.valueOf(v));
        });
        return n;
    }

    // ---------------- FR-32 创作者画像 ----------------
    @GetMapping(value = "/api/profile", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode getProfile() {
        ObjectNode out = mapper.createObjectNode();
        Optional<Store.CreatorProfileRow> p = store.getProfile();
        if (p.isEmpty()) {
            out.put("brand_voice", "");
            out.put("domain", "");
            out.put("audience", "");
            out.put("avoid", "");
            out.put("updated_at", "");
            out.put("exists", false);
            return out;
        }
        Store.CreatorProfileRow row = p.get();
        out.put("brand_voice", row.brandVoice());
        out.put("domain", row.domain());
        out.put("audience", row.audience());
        out.put("avoid", row.avoid());
        out.put("updated_at", row.updatedAt());
        out.put("exists", true);
        return out;
    }

    @PutMapping(value = "/api/profile", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode upsertProfile(@Valid @RequestBody ProfileUpsertRequest req) {
        store.upsertProfile(req.brand_voice(), req.domain(), req.audience(), req.avoid());
        log.info("profile updated: domain={} audience={}", req.domain(), req.audience());
        return getProfile();
    }
}