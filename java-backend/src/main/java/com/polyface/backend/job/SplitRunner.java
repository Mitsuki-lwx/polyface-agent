package com.polyface.backend.job;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
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
 * 素材拆分任务（`docs/spec_split.md`）：一个素材 → 多个**素材**。
 *
 * <p>拆出来的每条都落成真正的素材（进同一张 material 表），这样它能再走一遍完整管线。
 *
 * <p>**为什么是任务而不是同步调用**：拆分是一次 LLM 调用，长素材上要 1~3 分钟。
 * 同步调用时界面只能干等，进程被打断就什么都不知道了。走任务后：
 * 进度可看、能取消、被打断能接着跑。
 *
 * <p>**续跑的粒度是整批**（不是每条）：一次 LLM 调用产出全部子素材，
 * 中间没有可落盘的片段。所以 {@code split.json} 是完成标记 ——
 * 它存在就说明子素材**已经建好了**，续跑时直接复用，绝不能再建一遍
 * （否则续跑一次就多出一批重复素材，这是比"没跑完"更糟的结果）。
 */
@Component
public class SplitRunner {

    private static final Logger log = LoggerFactory.getLogger(SplitRunner.class);

    private final Store store;
    private final PythonClient python;
    private final ObjectMapper mapper = new ObjectMapper();

    private final Path jobsDir;
    private final int keepJobs;

    private final ExecutorService pool = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "split-job");
        t.setDaemon(true);
        return t;
    });

    /** 取消是协作式的：在途的那次 LLM 调用拦不住，只能在发起前停手。 */
    private final Set<Long> cancelRequested = Collections.synchronizedSet(new LinkedHashSet<>());

    public SplitRunner(Store store, PythonClient python,
                       @Value("${polyface.data-dir:../data}") String dataDir,
                       @Value("${polyface.roughcut.keep-jobs:50}") int keepJobs) {
        this.store = store;
        this.python = python;
        this.jobsDir = Path.of(dataDir).toAbsolutePath().resolve("jobs");
        this.keepJobs = keepJobs;
    }

    // ---------------- 提交 ----------------

    public long submit(long materialId, List<String> angles, List<String> formats) {
        Path dir = jobsDir.resolve("job-" + System.nanoTime());
        try {
            Files.createDirectories(dir);
        } catch (IOException e) {
            throw new IllegalStateException("无法创建任务目录：" + e.getMessage(), e);
        }
        ObjectNode params = mapper.createObjectNode();
        params.put("material_id", materialId);
        ArrayNode a = params.putArray("angles");
        angles.forEach(a::add);
        ArrayNode f = params.putArray("formats");
        formats.forEach(f::add);
        long jobId = store.insertJob("split", params.toString(), null,
                String.valueOf(materialId), dir.toString(), dir.resolve("events.jsonl").toString());
        store.pruneJobs(keepJobs);
        pool.submit(() -> execute(jobId, materialId, angles, formats, dir));
        return jobId;
    }

    /** 请求取消。在途的那次调用拦不住，但结果会被丢弃（子素材不落库）。 */
    public boolean cancel(long jobId) {
        cancelRequested.add(jobId);
        return true;
    }

    /**
     * 接着跑一个「已中断」的拆分任务。
     *
     * <p>**必须复用原来的任务目录** —— `split.json` 就在那里，它是"子素材已经建好了"的凭据。
     * 换个新目录就等于从头拆一遍，会**重复建出一批素材**（比没跑完更糟）。
     */
    public boolean resume(long jobId) {
        Store.JobRow j = store.getJob(jobId).orElse(null);
        if (j == null || !"split".equals(j.kind())) {
            return false;
        }
        long materialId;
        List<String> angles;
        List<String> formats;
        try {
            JsonNode p = mapper.readTree(j.paramsJson() == null ? "{}" : j.paramsJson());
            materialId = p.path("material_id").asLong();
            angles = new java.util.ArrayList<>();
            p.path("angles").forEach(n -> angles.add(n.asText()));
            formats = new java.util.ArrayList<>();
            p.path("formats").forEach(n -> formats.add(n.asText()));
        } catch (Exception e) {                                  // noqa: BLE001
            log.warn("任务 {} 的参数读不出来，无法续跑：{}", jobId, e.getMessage());
            return false;
        }
        if (materialId <= 0) {
            return false;
        }
        store.markJobRunning(jobId);
        Path dir = Path.of(j.workdir());
        pool.submit(() -> execute(jobId, materialId, angles, formats, dir));
        return true;
    }

    // ---------------- 执行 ----------------

    /** 公开：测试要同步调它（不然只能轮询，测试会飘）。 */
    public void execute(long jobId, long materialId, List<String> angles, List<String> formats,
                 Path dir) {
        store.markJobRunning(jobId);
        Path events = dir.resolve("events.jsonl");
        Path record = dir.resolve("split.json");
        try {
            // ① 续跑：`split.json` 在 = 子素材已建好。直接复用，**绝不再建一遍**。
            if (Files.isRegularFile(record)) {
                JsonNode prev = mapper.readTree(Files.readString(record, StandardCharsets.UTF_8));
                log.info("任务 {} 续跑：拆分记录已存在，复用 {} 条子素材", jobId, prev.path("count").asInt());
                appendEvent(events, "复用", "end", Map.of("count", prev.path("count").asInt()));
                store.finishJobWithResult(jobId, Store.JOB_SUCCEEDED, null, prev.toString());
                return;
            }

            Store.MaterialRow m = store.getMaterial(materialId).orElse(null);
            if (m == null) {
                store.finishJobWithResult(jobId, Store.JOB_FAILED, "素材不存在（可能已被删除）", "{}");
                return;
            }
            if (cancelRequested.contains(jobId)) {
                store.finishJobWithResult(jobId, Store.JOB_CANCELED, "已取消", "{}");
                return;
            }

            // ② 事实清单原样带上：拆分的**底线**是子素材的事实只能来自这里
            ObjectNode body = mapper.createObjectNode();
            body.put("raw_text", m.rawText());
            ObjectNode st = body.putObject("structured");
            st.put("core_message", m.coreMessage() == null ? "" : m.coreMessage());
            st.put("tone", m.tone() == null ? "" : m.tone());
            st.put("audience", m.audience() == null ? "" : m.audience());
            ArrayNode facts = st.putArray("facts");
            try {
                for (JsonNode f : mapper.readTree(m.factsJson() == null ? "[]" : m.factsJson())) {
                    facts.add(f);
                }
            } catch (Exception e) {                              // noqa: BLE001
                // 事实读不出来 → 明确拒绝，而不是让子素材"没有事实"（那就失去底线了）
                store.finishJobWithResult(jobId, Store.JOB_FAILED,
                        "这个素材的事实清单读不出来，先修好它再拆：" + e.getMessage(), "{}");
                return;
            }
            ArrayNode a = body.putArray("angles");
            angles.forEach(a::add);
            ArrayNode f = body.putArray("formats");
            formats.forEach(f::add);

            appendEvent(events, "拆分", "start",
                    Map.of("angles", angles.size(), "formats", formats.size(),
                           "facts", facts.size()));
            JsonNode resp = python.splitMaterial(body);          // ← 唯一一次 LLM 调用

            // 调用中途用户点了取消：**结果丢掉，不落库**。
            // 在途的 HTTP 调用拦不住，但"已经不要了"的结果不该变成一批素材
            // （不然用户取消了却多出一批素材，比取消不了还糟）。
            if (cancelRequested.contains(jobId)) {
                log.info("任务 {} 在模型返回前被取消，结果已丢弃（未建任何子素材）", jobId);
                appendEvent(events, "拆分", "end", Map.of("canceled", true));
                store.finishJobWithResult(jobId, Store.JOB_CANCELED,
                        "已取消（模型调用已返回，结果未采用）", "{}");
                return;
            }

            // ③ 落成**真正的素材**
            ObjectNode out = mapper.createObjectNode();
            out.put("parent_id", materialId);
            ArrayNode items = out.putArray("items");
            for (JsonNode item : resp.path("items")) {
                String title = item.path("title").asText("");
                String core = item.path("core_message").asText("");
                String angle = item.path("angle").asText("");
                String format = item.path("format").asText("");
                long childId = store.insertChildMaterial(
                        m.rawText(), title.isBlank() ? core : title, core,
                        m.tone(), m.audience(), item.path("facts").toString(),
                        materialId, angle, format);
                ObjectNode one = items.addObject();
                one.put("id", childId);
                one.put("angle", angle);
                one.put("format", format);
                one.put("title", title);
                one.put("core_message", core);
                one.put("fact_count", item.path("facts").size());
                one.set("facts", item.path("facts"));   // 事实出处，逐条可复核
                appendEvent(events, "子素材", "end",
                        Map.of("id", childId, "angle", angle, "facts", item.path("facts").size()));
            }
            out.put("count", items.size());

            // ④ **先写记录再收尾**：记录是续跑的依据，写它必须早于"任务成功"。
            //    反过来（先标成功再写记录）会在两者之间留一个"已成功但记录缺失"的窗口。
            Files.writeString(record, out.toString(), StandardCharsets.UTF_8);
            appendEvent(events, "拆分", "end", Map.of("count", items.size()));
            store.finishJobWithResult(jobId, Store.JOB_SUCCEEDED, null, out.toString());
            log.info("素材 {} 拆出 {} 条子素材（任务 {}）", materialId, items.size(), jobId);

        } catch (Exception e) {                                  // noqa: BLE001
            log.warn("拆分任务 {} 失败：{}", jobId, e.getMessage());
            store.finishJobWithResult(jobId, Store.JOB_FAILED, "拆分失败：" + e.getMessage(), "{}");
        } finally {
            cancelRequested.remove(jobId);
        }
    }

    /** 任务记录（供事件流用）。 */
    private void appendEvent(Path events, String phase, String kind, Map<String, Object> extra) {
        try {
            ObjectNode n = mapper.createObjectNode();
            n.put("ts", java.time.LocalDateTime.now().toString());
            n.put("phase", phase);
            n.put("kind", kind);
            extra.forEach(n::putPOJO);
            Files.writeString(events, n.toString() + "\n", StandardCharsets.UTF_8,
                    java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
        } catch (Exception e) {                                  // noqa: BLE001
            log.debug("事件写入失败（不影响任务）：{}", e.getMessage());
        }
    }
}
