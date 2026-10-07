package com.polyface.backend.job;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 生成任务编排（M8 第二片）：**理解一次 + 每平台一次调用**。
 *
 * <p>为什么这么拆（`docs/spec_async_generate.md` §5）：一次大调用会因多平台串行累加而超时；
 * 拆开后①每次调用都落在既有单次预算内，②平台级进度天然可得，③失败平台可以单独重跑。
 *
 * <p>**"理解只跑一次"靠 `confirmed_facts`**：它在 `GenerateRequest` 里存在时 Python 会跳过理解阶段。
 * 所以本类在循环**之前**先把事实准备好（已确认的就用，没确认的就先理解一次），
 * 之后每个平台都带着同一份事实调用 —— 最贵的那次调用不会被重复 N 遍。
 */
@Component
public class GenerateRunner {

    private static final Logger log = LoggerFactory.getLogger(GenerateRunner.class);

    private final Store store;
    private final PythonClient python;
    private final GenerateService generateService;
    private final ObjectMapper mapper = new ObjectMapper();

    private final Path jobsDir;
    private final int stallSec;
    private final int keepJobs;

    private final ExecutorService pool = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "generate-job");
        t.setDaemon(true);
        return t;
    });

    /** 取消是**协作式**的：没有子进程可杀，只能在下个平台前停手（在途的那次 HTTP 调用拦不住）。 */
    private final Set<Long> cancelRequested = Collections.synchronizedSet(new LinkedHashSet<>());

    private final Map<Long, Boolean> active = new ConcurrentHashMap<>();

    public GenerateRunner(Store store, PythonClient python, GenerateService generateService,
                          @Value("${polyface.data-dir:../data}") String dataDir,
                          @Value("${polyface.roughcut.stall-sec:300}") int stallSec,
                          @Value("${polyface.roughcut.keep-jobs:50}") int keepJobs) {
        this.store = store;
        this.python = python;
        this.generateService = generateService;
        this.jobsDir = Path.of(dataDir).toAbsolutePath().resolve("jobs");
        this.stallSec = stallSec;
        this.keepJobs = keepJobs;
        recoverInterrupted();
    }

    /** 与粗剪同样的启动清理：上次没跑完的标为失败，否则工作台会永远转圈。 */
    private void recoverInterrupted() {
        try {
            List<Store.JobRow> inFlight = store.jobsInFlight();
            for (Store.JobRow j : inFlight) {
                store.finishJob(j.id(), Store.JOB_FAILED,
                        "服务重启时任务尚未结束", null, null, null);
            }
            if (!inFlight.isEmpty()) {
                log.warn("启动清理：{} 个任务在上次运行中未结束，已标记为失败", inFlight.size());
            }
        } catch (Exception e) {
            log.warn("启动清理未完成：{}", e.getMessage());
        }
    }

    // ---------------- 提交 ----------------

    /** 发起一次生成任务。`platforms` 为空则按全部可用平台。 */
    public long submit(long materialId, List<String> platforms, Long templateId,
                       JsonNode inlineTemplate, String toneOverride) {
        Path dir = jobsDir.resolve("job-" + System.nanoTime());
        try {
            Files.createDirectories(dir);
        } catch (IOException e) {
            throw new IllegalStateException("无法创建任务目录：" + e.getMessage(), e);
        }
        ObjectNode params = mapper.createObjectNode();
        params.put("material_id", materialId);
        ArrayNode arr = params.putArray("platforms");
        platforms.forEach(arr::add);
        if (templateId != null) {
            params.put("template_id", templateId);
        }
        if (inlineTemplate != null && !inlineTemplate.isNull()) {
            // "临时模板（不保存，仅本次生成）" —— 走任务也不能丢这个能力
            params.set("template", inlineTemplate);
        }
        if (toneOverride != null) {
            params.put("tone_override", toneOverride);
        }
        long jobId = store.insertJob("generate", params.toString(), null,
                String.valueOf(materialId), dir.toString(), dir.resolve("events.jsonl").toString());
        store.pruneJobs(keepJobs);
        pool.submit(() -> execute(jobId, materialId, platforms, templateId, inlineTemplate,
                toneOverride, dir));
        return jobId;
    }

    private void execute(long jobId, long materialId, List<String> platforms, Long templateId,
                         JsonNode inlineTemplate, String toneOverride, Path dir) {
        store.markJobRunning(jobId);
        active.put(jobId, Boolean.TRUE);
        Path events = dir.resolve("events.jsonl");
        ObjectNode result = mapper.createObjectNode();
        ArrayNode okArr = result.putArray("ok");
        ArrayNode failArr = result.putArray("failed");
        String fatal = null;
        try {
            Store.MaterialRow m = store.getMaterial(materialId).orElse(null);
            if (m == null) {
                fatal = "素材不存在（可能已被删除）";
            } else {
                JsonNode template = (inlineTemplate != null && !inlineTemplate.isNull())
                        ? inlineTemplate : resolveTemplate(templateId);
                // ① 事实先备好 —— 这是"理解只跑一次"的关键
                ObjectNode facts = ensureFacts(jobId, m, events, dir);
                // ② 每平台一次调用
                for (int i = 0; i < platforms.size(); i++) {
                    String code = platforms.get(i);
                    if (cancelRequested.contains(jobId)) {
                        fatal = null;
                        break;
                    }
                    appendEvent(events, "平台", "start",
                            Map.of("index", i + 1, "total", platforms.size(), "platform", code));
                    try {
                        ObjectNode body = generateService.buildBody(m, List.of(code), template,
                                toneOverride, events.toString());
                        if (facts != null) {
                            body.set("confirmed_facts", facts);   // 复用同一份事实
                        }
                        JsonNode resp = generateService.callPython(body);
                        List<GenerateService.StoredDraft> stored =
                                generateService.storeDrafts(materialId, resp.path("drafts"),
                                        templateId, templateVersion(templateId));
                        for (GenerateService.StoredDraft d : stored) {
                            ObjectNode n = okArr.addObject();
                            n.put("platform_code", d.platformCode());
                            n.put("platform_name", d.platformName());
                            n.put("draft_id", d.id());
                            n.put("status", d.status());
                        }
                        appendEvent(events, "平台", "end", Map.of("platform", code, "drafts", stored.size()));
                    } catch (Exception e) {                     // noqa: BLE001 —— 单平台失败可容忍
                        log.warn("生成平台 {} 失败：{}", code, e.getMessage());
                        ObjectNode n = failArr.addObject();
                        n.put("platform_code", code);
                        n.put("error", shorten(e.getMessage(), 300));
                        appendEvent(events, "平台", "fail", Map.of("platform", code,
                                "error", shorten(e.getMessage(), 200)));
                    }
                }
            }
        } catch (Exception e) {                                  // noqa: BLE001
            fatal = shorten(e.getMessage(), 500);
            log.error("生成任务 {} 异常", jobId, e);
        } finally {
            active.remove(jobId);
        }

        boolean canceled = cancelRequested.remove(jobId)
                || Store.JOB_CANCELED.equals(store.getJob(jobId).map(Store.JobRow::status).orElse(""));
        if (canceled) {
            store.finishJobWithResult(jobId, Store.JOB_CANCELED, "已取消", result.toString());
            return;
        }
        int ok = okArr.size();
        int failed = failArr.size();
        String status;
        String error = null;
        if (fatal != null) {
            status = Store.JOB_FAILED;
            error = fatal;
        } else if (failed == 0 && ok > 0) {
            status = Store.JOB_SUCCEEDED;
        } else if (ok == 0) {
            status = Store.JOB_FAILED;
            error = "所有平台都失败了：" + summarizeFailures(failArr);
        } else {
            // 部分失败：既不能记成功（会藏起失败），也不能记失败（会抹掉已产出的稿）
            status = Store.JOB_PARTIAL;
            error = "部分平台失败：" + summarizeFailures(failArr);
        }
        result.put("ok_count", ok);
        result.put("fail_count", failed);
        store.finishJobWithResult(jobId, status, error, result.toString());
        log.info("生成任务 {} 结束 status={} ok={} failed={}", jobId, status, ok, failed);
    }

    /**
     * 把事实准备好：已确认的直接用，没确认的**先理解一次**。
     *
     * <p>这一步就是"理解只跑一次"的保证 —— 之后每个平台都复用同一份，不会再触发理解。
     */
    private ObjectNode ensureFacts(long jobId, Store.MaterialRow m, Path events, Path dir) {
        ObjectNode confirmed = generateService.confirmedFacts(m);
        if (confirmed != null) {
            appendEvent(events, "理解", "end", Map.of("skipped", true, "reason", "已确认事实"));
            return confirmed;
        }
        appendEvent(events, "理解", "start", Map.of());
        try {
            JsonNode analyzed = python.analyze(m.rawText(),
                    m.sourceKind() == null ? "general" : m.sourceKind(), m.title());
            JsonNode structured = analyzed.path("structured");
            if (structured.isMissingNode() || structured.isNull()) {
                appendEvent(events, "理解", "fail", Map.of("error", "分析结果为空"));
                return null;      // 拿不到就让各平台各自理解（退化为旧行为，但至少能跑）
            }
            ObjectNode facts = (ObjectNode) structured;
            appendEvent(events, "理解", "end", Map.of(
                    "facts", facts.path("facts").size()));
            return facts;
        } catch (Exception e) {                                  // noqa: BLE001
            log.warn("任务 {} 的理解阶段失败，退化为各平台各自理解：{}", jobId, e.getMessage());
            appendEvent(events, "理解", "fail", Map.of("error", shorten(e.getMessage(), 200)));
            return null;
        }
    }

    private JsonNode resolveTemplate(Long templateId) {
        if (templateId == null) {
            return null;
        }
        return store.getTemplate(templateId)
                .map(t -> (JsonNode) com.polyface.backend.web.TemplateMapper.toUserTemplate(t))
                .orElse(null);
    }

    private Integer templateVersion(Long templateId) {
        return templateId == null ? null : store.getTemplate(templateId)
                .map(Store.TemplateRow::version).orElse(null);
    }

    /**
     * 往事件文件里追加一条**与 Python 同 schema** 的事件。
     *
     * <p>为什么自己写而不是让 Python 报平台边界：Java 才是编排者，只有它知道"第几个平台"。
     * 两个进程追加同一个文件是**刻意的取舍**（spec §5）：两边都是"一行一条、写完即关"，
     * 而读取端本来就会跳过坏行 —— 最坏丢一行进度，不会崩。
     */
    private void appendEvent(Path events, String stage, String kind, Map<String, Object> fields) {
        try {
            ObjectNode ev = mapper.createObjectNode();
            ev.put("run_id", "java");
            ev.put("stage", stage);
            ev.put("kind", kind);
            ev.put("ts", java.time.OffsetDateTime.now().toString());
            ev.put("elapsed_ms", 0);
            ev.put("message", kind.equals("start") ? "" : String.valueOf(fields.getOrDefault("reason",
                    fields.getOrDefault("platform", ""))));
            ev.put("version", 1);
            ObjectNode f = ev.putObject("fields");
            fields.forEach((k, v) -> f.putPOJO(k, v));
            Files.writeString(events, ev.toString() + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    Files.exists(events) ? java.nio.file.StandardOpenOption.APPEND
                            : java.nio.file.StandardOpenOption.CREATE);
        } catch (Exception e) {                                  // noqa: BLE001 —— 观测绝不影响业务
            log.warn("写平台事件失败（不影响任务）：{}", e.getMessage());
        }
    }

    private static String summarizeFailures(ArrayNode failArr) {
        List<String> parts = new ArrayList<>();
        failArr.forEach(n -> parts.add(n.path("platform_code").asText("?") + "：" +
                n.path("error").asText("")));
        return String.join("；", parts);
    }

    private static String shorten(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() <= max ? s : s.substring(0, max) + "…";
    }

    // ---------------- 查询与取消 ----------------

    public Optional<JobProgress> progress(Store.JobRow job) {
        return Optional.of(JobProgress.read(
                job.eventsPath() == null ? null : Path.of(job.eventsPath()),
                Store.JOB_RUNNING.equals(job.status()), stallSec));
    }

    /** 协作式取消：标记后，编排循环会在**下一个平台之前**停手。 */
    public boolean cancel(long jobId) {
        if (!active.containsKey(jobId)) {
            return false;
        }
        cancelRequested.add(jobId);
        return true;
    }

    public boolean isRunning(long jobId) {
        return active.containsKey(jobId);
    }

    /** 正在跑的任务数（单机串行，正常是 0 或 1）。 */
    public int activeCount() {
        return active.size();
    }
}
