package com.polyface.backend.web;

import java.nio.file.Path;
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
import com.polyface.backend.job.JobProgress;
import com.polyface.backend.job.RoughcutRunner;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 异步任务（M8 第一片）：粗剪的发起 / 查询 / 取消。
 *
 * <p>与既有控制器的分工一致：这里只做参数整形与状态码，
 * 执行在 {@link RoughcutRunner}，状态在 {@link Store}，进度从**阶段事件文件**读。
 */
@RestController
public class JobController {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    private final Store store;
    private final RoughcutRunner runner;
    private final AssetService assets;
    private final MediaDir mediaDir;
    private final ObjectMapper mapper = new ObjectMapper();

    public JobController(Store store, RoughcutRunner runner, AssetService assets, MediaDir mediaDir) {
        this.store = store;
        this.runner = runner;
        this.assets = assets;
        this.mediaDir = mediaDir;
    }

    /** 发起请求体。`params` 只认 UI 会传的那几个；没传的用 CLI 自己的默认。 */
    public record StartBody(String kind, Long asset_id, Map<String, Object> params) {
    }

    // ---------------- 发起 ----------------

    @PostMapping(value = "/api/jobs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode start(@RequestBody StartBody body) {
        if (body == null || body.asset_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 asset_id");
        }
        String kind = body.kind() == null || body.kind().isBlank() ? "roughcut" : body.kind();
        if (!"roughcut".equals(kind)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "暂不支持的任务类型：" + kind);
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
        String baseName = "out";
        long jobId = runner.submit(row.id(), file.toString(), params, baseName);

        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", jobId);
        out.put("status", Store.JOB_QUEUED);
        return out;
    }

    // ---------------- 查询 ----------------

    @GetMapping(value = "/api/jobs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode list(@RequestParam(value = "limit", defaultValue = "20") int limit) {
        ObjectNode out = mapper.createObjectNode();
        ArrayNode items = out.putArray("items");
        for (Store.JobRow j : store.listJobs(Math.min(Math.max(limit, 1), MAX_LIMIT))) {
            items.add(toDto(j));
        }
        return out;
    }

    @GetMapping(value = "/api/jobs/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode get(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        return toDto(j);
    }

    /** 取消：**杀进程树**。幂等 —— 已经结束的任务返回 200 且状态不变。 */
    @PostMapping(value = "/api/jobs/{id}/cancel", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode cancel(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        boolean killed = runner.cancel(id);
        if (killed) {
            // 先把状态置成 canceled，让执行线程在 finally 里认出"这是被取消的"，而不是记成失败
            store.finishJob(id, Store.JOB_CANCELED, "已取消", null, null, null);
        }
        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", id);
        out.put("canceled", killed);
        out.put("status", store.getJob(id).map(Store.JobRow::status).orElse(j.status()));
        return out;
    }

    // ---------------- 参数默认值 ----------------

    /** 把 CLI 的默认值原样转出去 —— **默认值的唯一来源是 Python 侧**，不在 Java 里抄。 */
    @GetMapping(value = "/api/jobs/params", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode params() {
        return runner.defaults();
    }

    /** 自检：把生效的路径与并发情况暴露出来（"启用了"要有可查询证据）。 */
    @GetMapping(value = "/api/jobs/health", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode health() {
        return mapper.valueToTree(runner.describe());
    }

    // ---------------- DTO ----------------

    private ObjectNode toDto(Store.JobRow j) {
        ObjectNode n = mapper.createObjectNode();
        n.put("id", j.id());
        n.put("kind", j.kind());
        n.put("status", j.status());
        if (j.inputAssetId() != null) {
            n.put("input_asset_id", j.inputAssetId());
            assets.get(j.inputAssetId()).ifPresent(a -> n.put("input_name", a.name()));
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

        JobProgress p = runner.progress(j).orElseGet(JobProgress::empty);
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

        // 产物：给可访问的 url（复用 /api/media，不新开端点）
        ObjectNode outs = n.putObject("outputs");
        putUrl(outs, "video_url", j.outputPath());
        putUrl(outs, "srt_url", j.srtPath());
        putUrl(outs, "cuts_url", j.cutsPath());
        return n;
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
}
