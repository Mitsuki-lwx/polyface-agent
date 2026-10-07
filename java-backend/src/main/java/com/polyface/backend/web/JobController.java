package com.polyface.backend.web;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.job.GenerateRunner;
import com.polyface.backend.job.JobProgress;
import com.polyface.backend.job.RoughcutRunner;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 异步任务：粗剪（M8 第一片）与生成（第二片）的发起 / 查询 / 取消 / 重试。
 *
 * <p>分工与既有控制器一致：这里只做参数整形与状态码；
 * 执行在两个 Runner，状态在 {@link Store}，进度从**阶段事件文件**读。
 */
@RestController
public class JobController {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    public static final String KIND_ROUGHCUT = "roughcut";
    public static final String KIND_GENERATE = "generate";

    private final Store store;
    private final RoughcutRunner roughcut;
    private final GenerateRunner generate;
    private final AssetService assets;
    private final MediaDir mediaDir;
    private final ObjectMapper mapper = new ObjectMapper();

    public JobController(Store store, RoughcutRunner roughcut, GenerateRunner generate,
                         AssetService assets, MediaDir mediaDir) {
        this.store = store;
        this.roughcut = roughcut;
        this.generate = generate;
        this.assets = assets;
        this.mediaDir = mediaDir;
    }

    /** 发起请求体。粗剪用 `asset_id`；生成用 `material_id` + `platforms`。 */
    public record StartBody(String kind, Long asset_id, Long material_id, List<String> platforms,
                            Long template_id, JsonNode template, String tone_override,
                            Map<String, Object> params) {
    }

    // ---------------- 发起 ----------------

    @PostMapping(value = "/api/jobs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode start(@RequestBody StartBody body) {
        if (body == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少请求体");
        }
        String kind = body.kind() == null || body.kind().isBlank() ? KIND_ROUGHCUT : body.kind();
        long jobId = switch (kind) {
            case KIND_ROUGHCUT -> startRoughcut(body);
            case KIND_GENERATE -> startGenerate(body);
            default -> throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "暂不支持的任务类型：" + kind);
        };
        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", jobId);
        out.put("kind", kind);
        out.put("status", Store.JOB_QUEUED);
        return out;
    }

    private long startRoughcut(StartBody body) {
        if (body.asset_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 asset_id");
        }
        Store.AssetRow row = assets.get(body.asset_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        if (!"video".equals(row.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "只有视频能粗剪，当前资产是 " + row.kind());
        }
        Path file = mediaDir.resolveSafe(row.relPath());
        if (!java.nio.file.Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "资产文件不在磁盘上：" + row.relPath());
        }
        Map<String, Object> params = body.params() == null ? Map.of() : body.params();
        return roughcut.submit(row.id(), file.toString(), params, "out");
    }

    private long startGenerate(StartBody body) {
        if (body.material_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 material_id");
        }
        store.getMaterial(body.material_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "素材不存在"));
        List<String> platforms = body.platforms() == null || body.platforms().isEmpty()
                ? List.of("xhs") : body.platforms();
        return generate.submit(body.material_id(), platforms, body.template_id(),
                body.template(), body.tone_override());
    }

    // ---------------- 重试失败平台 ----------------

    /**
     * 只重跑**失败的平台**，且**新建一个任务**。
     *
     * <p>为什么必须新建：原任务记录的是"上次跑了什么、结果如何" —— 就地改它，
     * 那段历史就查不到了（`docs/checklist_async_generate.md` §5）。
     */
    @PostMapping(value = "/api/jobs/{id}/retry", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode retry(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        if (!KIND_GENERATE.equals(j.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "只有生成任务支持重试");
        }
        List<String> failed = failedPlatforms(j);
        if (failed.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "这个任务没有失败的平台");
        }
        ObjectNode params = readJson(j.paramsJson());
        long materialId = params.path("material_id").asLong(0);
        Long templateId = params.has("template_id") ? params.path("template_id").asLong() : null;
        String tone = params.path("tone_override").asText(null);
        JsonNode inlineTpl = params.has("template") ? params.path("template") : null;
        long newId = generate.submit(materialId, failed, templateId, inlineTpl, tone);

        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", newId);
        out.put("retried_from", id);
        ArrayNode arr = out.putArray("platforms");
        failed.forEach(arr::add);
        return out;
    }

    /** 从任务结果里取出失败的平台列表。 */
    private List<String> failedPlatforms(Store.JobRow j) {
        List<String> out = new ArrayList<>();
        for (JsonNode n : readJson(j.resultJson()).path("failed")) {
            String code = n.path("platform_code").asText("");
            if (!code.isBlank()) {
                out.add(code);
            }
        }
        return out;
    }

    // ---------------- 查询 ----------------

    @GetMapping(value = "/api/jobs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode list(@RequestParam(value = "limit", defaultValue = "20") int limit,
                           @RequestParam(value = "kind", required = false) String kind) {
        ObjectNode out = mapper.createObjectNode();
        ArrayNode items = out.putArray("items");
        for (Store.JobRow j : store.listJobs(Math.min(Math.max(limit, 1), MAX_LIMIT))) {
            if (kind != null && !kind.isBlank() && !kind.equals(j.kind())) {
                continue;
            }
            items.add(toDto(j));
        }
        return out;
    }

    @GetMapping(value = "/api/jobs/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode get(@PathVariable long id) {
        return toDto(store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在")));
    }

    @PostMapping(value = "/api/jobs/{id}/cancel", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode cancel(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        boolean killed = KIND_GENERATE.equals(j.kind()) ? generate.cancel(id) : roughcut.cancel(id);
        if (killed) {
            store.finishJob(id, Store.JOB_CANCELED, "已取消", null, null, null);
        }
        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", id);
        out.put("canceled", killed);
        out.put("status", store.getJob(id).map(Store.JobRow::status).orElse(j.status()));
        return out;
    }

    @GetMapping(value = "/api/jobs/params", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode params() {
        return roughcut.defaults();
    }

    @GetMapping(value = "/api/jobs/health", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode health() {
        ObjectNode out = mapper.createObjectNode();
        out.set("roughcut", mapper.valueToTree(roughcut.describe()));
        out.put("generate_active", generate.activeCount());
        return out;
    }

    // ---------------- DTO ----------------

    private ObjectNode toDto(Store.JobRow j) {
        ObjectNode n = mapper.createObjectNode();
        n.put("id", j.id());
        n.put("kind", j.kind());
        n.put("status", j.status());
        if (KIND_ROUGHCUT.equals(j.kind()) && j.inputAssetId() != null) {
            n.put("input_asset_id", j.inputAssetId());
            assets.get(j.inputAssetId()).ifPresent(a -> n.put("input_name", a.name()));
        }
        if (KIND_GENERATE.equals(j.kind())) {
            ObjectNode params = readJson(j.paramsJson());
            n.put("material_id", params.path("material_id").asLong(0));
            n.set("platforms", params.path("platforms"));
            store.getMaterial(params.path("material_id").asLong(0))
                    .ifPresent(m -> n.put("input_name", m.title() == null ? m.coreMessage() : m.title()));
        }
        n.put("created_at", j.createdAt());
        if (j.startedAt() != null) {
            n.put("started_at", j.startedAt());
        }
        if (j.finishedAt() != null) {
            n.put("finished_at", j.finishedAt());
        }
        if (j.error() != null && !j.error().isBlank()) {
            n.put("error", j.error());
        }
        if (j.resultJson() != null && !j.resultJson().isBlank()) {
            n.set("result", readJson(j.resultJson()));
            List<String> failed = failedPlatforms(j);
            n.put("failed_count", failed.size());
            n.put("can_retry", !failed.isEmpty()
                    && !Store.JOB_RUNNING.equals(j.status()) && !Store.JOB_QUEUED.equals(j.status()));
        }

        JobProgress p = progressOf(j).orElseGet(JobProgress::empty);
        ObjectNode pn = n.putObject("progress");
        pn.put("stage", p.stage());
        if (p.pct() != null) {
            pn.put("pct", p.pct());
            pn.put("pct_stage", p.pctStage());
        }
        ArrayNode done = pn.putArray("done_stages");
        p.doneStages().forEach(done::add);
        pn.put("last_message", p.lastMessage());
        pn.put("stalled", p.stalled());

        ObjectNode outs = n.putObject("outputs");
        putUrl(outs, "video_url", j.outputPath());
        putUrl(outs, "srt_url", j.srtPath());
        putUrl(outs, "cuts_url", j.cutsPath());
        return n;
    }

    private java.util.Optional<JobProgress> progressOf(Store.JobRow j) {
        return KIND_GENERATE.equals(j.kind()) ? generate.progress(j) : roughcut.progress(j);
    }

    private void putUrl(ObjectNode outs, String key, String absPath) {
        if (absPath == null || absPath.isBlank()) {
            return;
        }
        try {
            Path p = Path.of(absPath).toAbsolutePath().normalize();
            if (!p.startsWith(mediaDir.root())) {
                return;
            }
            outs.put(key, MediaController.mediaUrl(
                    mediaDir.root().relativize(p).toString().replace('\\', '/')));
        } catch (Exception ignored) {
            // 路径异常就当没有这个产物，不让详情接口崩
        }
    }

    private ObjectNode readJson(String raw) {
        if (raw == null || raw.isBlank()) {
            return mapper.createObjectNode();
        }
        try {
            JsonNode n = mapper.readTree(raw);
            return n.isObject() ? (ObjectNode) n : mapper.createObjectNode();
        } catch (Exception e) {
            return mapper.createObjectNode();
        }
    }
}
