package com.polyface.backend.job;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.observability.TraceContext;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 按场景切段任务（`docs/spec_scene_split.md`，tasks T7）—— **两段式**：
 *
 * <ol>
 *   <li><b>方案阶段</b>（CLI 的 {@code --dry-run}）：检测切点 + 抽缩略图，
 *       产出 {@code work/scene-plan.json}，<b>不切任何视频</b>；</li>
 *   <li><b>切段阶段</b>：用户勾选后带 {@code plan_job_id} + {@code pick} 再发一次，
 *       <b>复用方案任务的目录</b>（计划文件在那里），把选中的段切出来登记成素材。</li>
 * </ol>
 *
 * <p>与 CLI 的契约和粗剪同构：Java 起子进程跑 {@code scripts/scene_split.py}，
 * 用阶段事件文件报进度（格式与 {@code roughcut.py} 完全一致，{@link JobProgress} 直接读）。
 * 进程管理照抄 {@link RoughcutRunner}：有界线程池、杀进程树、取消/中断语义一致。
 */
@Component
public class SceneSplitRunner {

    private static final Logger log = LoggerFactory.getLogger(SceneSplitRunner.class);

    public static final String KIND_SCENE_SPLIT = "scene_split";

    private final Store store;
    private final MediaDir mediaDir;
    private final AssetService assets;

    private final String python;
    private final String script;
    private final int stallSec;
    private final int keepJobs;

    private final ObjectMapper mapper = new ObjectMapper();

    /** 单线程：单机单用户，切段要解码整条视频，本来就吃满 CPU。 */
    private final ExecutorService pool = TraceContext.tracing(Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "scene-split-job");
        t.setDaemon(true);
        return t;
    }));

    private final Map<Long, Process> running = new ConcurrentHashMap<>();

    public SceneSplitRunner(Store store, MediaDir mediaDir, AssetService assets,
                            @Value("${polyface.roughcut.python:}") String python,
                            @Value("${polyface.roughcut.script:}") String script,
                            @Value("${polyface.roughcut.stall-sec:300}") int stallSec,
                            @Value("${polyface.roughcut.keep-jobs:50}") int keepJobs) {
        this.store = store;
        this.mediaDir = mediaDir;
        this.assets = assets;
        this.python = python == null || python.isBlank() ? "python-service/.venv/Scripts/python.exe" : python;
        this.script = script == null || script.isBlank() ? "scripts/roughcut.py" : script;
        this.stallSec = stallSec;
        this.keepJobs = keepJobs;
        // 退出时清理：杀掉还在跑的 job 子进程（含子孙）。见 JobReaper 的注释。
        JobReaper.install("scene-split-reaper", () -> java.util.List.copyOf(running.values()));
        // 不做自己的 recoverInterrupted：GenerateRunner.recoverInterrupted() 启动时按
        // in-flight 扫**全表**，对 scene_split 一视同仁地标「可继续」，这里不重复做。
    }

    // ---------------- 提交 ----------------

    /** ① 方案阶段：只出方案（--dry-run），不产出任何视频。 */
    public long submitPlan(Long assetId, String inputPath, Map<String, Object> params) {
        Path outDir = mediaDir.root().resolve("scenes").resolve("job-" + System.nanoTime());
        try {
            Files.createDirectories(outDir);
        } catch (IOException e) {
            throw new IllegalStateException("无法创建切段任务目录：" + e.getMessage(), e);
        }
        Map<String, Object> all = new LinkedHashMap<>(params == null ? Map.of() : params);
        String events = outDir.resolve("events.jsonl").toString();
        long jobId = store.insertJob(KIND_SCENE_SPLIT, toJson(all), assetId, inputPath,
                outDir.toString(), events);
        store.pruneJobs(keepJobs);
        pool.submit(() -> execute(jobId, false, inputPath, outDir, all));
        return jobId;
    }

    /**
     * ② 切段阶段：**复用方案任务的目录**（{@code scene-plan.json} 在那里），按 pick 切。
     *
     * <p>切段任务的检测参数从方案任务里读回（不靠前端再传一遍）—— 保证切出来的段
     * 与用户看到的方案是同一套切点，而不是"再检测一次可能不一样的"。
     */
    public long submitCut(Long assetId, String inputPath, long planJobId, List<Integer> pick) {
        if (pick == null || pick.isEmpty()) {
            throw new IllegalArgumentException("没有勾选任何段（不允许隐式全切）");
        }
        Store.JobRow planJob = store.getJob(planJobId).orElse(null);
        if (planJob == null || !KIND_SCENE_SPLIT.equals(planJob.kind())) {
            throw new IllegalArgumentException("plan_job_id 不是切场景的方案任务：" + planJobId);
        }
        Path outDir = Path.of(planJob.workdir());
        Map<String, Object> params = new LinkedHashMap<>(fromJson(planJob.paramsJson()));
        params.put("plan_job_id", planJobId);
        params.put("pick", new ArrayList<Integer>(pick));
        // 切段任务的事件流单独一份：与方案任务共用会把两段进度混在一起
        String events = outDir.resolve("events-cut.jsonl").toString();
        long jobId = store.insertJob(KIND_SCENE_SPLIT, toJson(params), assetId, inputPath,
                outDir.toString(), events);
        store.pruneJobs(keepJobs);
        pool.submit(() -> execute(jobId, true, inputPath, outDir, params));
        return jobId;
    }

    // ---------------- 续跑 / 取消 / 进度 ----------------

    /**
     * 接着跑一个「已中断」的切段任务：**沿用原来的任务目录**。
     *
     * <p>阶段（方案还是切段）从参数里认：有 {@code plan_job_id} 就是切段。
     * 切段的续跑不会重复登记素材 —— {@code registered.json} 是"已登记"的凭据，
     * 存在就直接复用（同 {@code SplitRunner} 的 split.json 逻辑）。
     */
    public boolean resume(long jobId) {
        Store.JobRow j = store.getJob(jobId).orElse(null);
        if (j == null || !KIND_SCENE_SPLIT.equals(j.kind())
                || !Store.JOB_INTERRUPTED.equals(j.status())) {
            return false;
        }
        Map<String, Object> params = fromJson(j.paramsJson());
        boolean cut = params.get("plan_job_id") != null;
        Path outDir = Path.of(j.workdir());
        store.markJobRunning(jobId);
        pool.submit(() -> execute(jobId, cut, j.inputPath(), outDir, params));
        return true;
    }

    /** 取消：**杀进程树**（与粗剪同一套语义）。 */
    public boolean cancel(long jobId) {
        Process p = running.get(jobId);
        if (p == null) {
            return false;
        }
        p.descendants().forEach(ProcessHandle::destroyForcibly);
        p.destroyForcibly();
        try {
            p.waitFor(5, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return true;
    }

    public boolean isRunning(long jobId) {
        return running.containsKey(jobId);
    }

    /** 事件格式与粗剪完全一致，直接复用同一套解析。 */
    public Optional<JobProgress> progress(Store.JobRow job) {
        boolean runningNow = Store.JOB_RUNNING.equals(job.status());
        return Optional.of(JobProgress.read(
                job.eventsPath() == null ? null : Path.of(job.eventsPath()), runningNow, stallSec));
    }

    // ---------------- 执行 ----------------

    private void execute(long jobId, boolean cut, String inputPath, Path outDir,
                         Map<String, Object> params) {
        store.markJobRunning(jobId);
        Path errFile = outDir.resolve(cut ? "cut-stderr.log" : "plan-stderr.log");
        int exit;
        try {
            List<String> cmd = buildCommand(cut, inputPath, outDir, params);
            log.info("job {} 启动：{}", jobId, String.join(" ", cmd));
            ProcessBuilder pb = new ProcessBuilder(cmd).directory(new java.io.File("."));
            // Windows 控制台默认 GBK：CLI 里的 ▶/✓ 符号会让 Python 直接崩
            // （scene_split.py 目前没调 setup_output_encoding）。PYTHONUTF8=1 是**本进程**
            // 的环境补丁，不改 Python 侧代码 —— 缺编码处理这件事单独上报。
            pb.environment().putIfAbsent("PYTHONUTF8", "1");
            pb.redirectErrorStream(true);
            pb.redirectOutput(errFile.toFile());
            Process p = pb.start();
            running.put(jobId, p);
            exit = p.waitFor();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            // 「被中断」不等于「失败」：方案/已切出的段都落盘在，可以继续。
            store.markJobInterrupted(jobId);
            return;
        } catch (IOException e) {
            store.finishJob(jobId, Store.JOB_FAILED, "启动失败：" + e.getMessage(), null, null, null);
            return;
        } finally {
            running.remove(jobId);
        }
        complete(jobId, cut, outDir, exit, readLogLenient(errFile));
    }

    /**
     * 产物/结果落地。**公开给测试**：不真跑子进程（不起 ffmpeg/Python）也能验证完成逻辑
     * （同 {@code SplitRunner.execute} 的公开理由 —— 只能同步调它，测试才不飘）。
     */
    public void complete(long jobId, boolean cut, Path outDir, int exit, String errText) {
        boolean canceled = Store.JOB_CANCELED.equals(store.getJob(jobId)
                .map(Store.JobRow::status).orElse(""));
        if (canceled) {
            store.finishJob(jobId, Store.JOB_CANCELED, "已取消", null, null, null);
            return;
        }
        if (exit != 0) {
            // 退出码 1 时 CLI 已把可读原因（含 PySceneDetect 的安装指引）写到 stderr
            store.finishJob(jobId, Store.JOB_FAILED, tail(errText, 500), null, null, null);
            log.warn("scene_split job {} 失败 exit={}：{}", jobId, exit, tail(errText, 200));
            return;
        }
        Path plan = outDir.resolve("work").resolve("scene-plan.json");
        if (!Files.isRegularFile(plan)) {
            // 切段阶段拿不到方案文件必须**明确失败** —— 静默产出 0 段比报错更糟
            store.finishJob(jobId, Store.JOB_FAILED,
                    (cut ? "方案任务的 scene-plan.json 不存在 —— 切段必须复用出方案那个任务的目录，"
                            + "请先对同一个视频重新出方案"
                          : "脚本正常退出但没有产出方案文件") + tail(errText, 300),
                    null, null, null);
            return;
        }
        JsonNode d;
        try {
            d = mapper.readTree(Files.readString(plan, StandardCharsets.UTF_8));
        } catch (Exception e) {                                  // noqa: BLE001
            store.finishJob(jobId, Store.JOB_FAILED, "方案文件读不出来：" + e.getMessage(),
                    null, null, null);
            return;
        }
        if (!cut) {
            store.finishJobWithResult(jobId, Store.JOB_SUCCEEDED, null, d.toString());
            log.info("job {} 方案完成：{} 段", jobId, d.path("count").asInt());
            return;
        }
        List<Integer> cutIdx = new ArrayList<>();
        d.path("cut").forEach(n -> cutIdx.add(n.asInt()));
        if (cutIdx.isEmpty()) {
            store.finishJobWithResult(jobId, Store.JOB_FAILED,
                    "方案里没有记录任何已切段（cut 为空）—— 请带着勾选重新发起切段", "{}");
            return;
        }
        registerCutSegments(jobId, outDir, d, cutIdx);
    }

    /**
     * 切出的每段**登记成素材** + 血缘挂回源视频。
     *
     * <p>登记是附加价值，失败不让任务算失败（沿用粗剪的既有约定）。
     * 续跑防重：{@code work/registered.json} 存在说明上次已登记过，直接复用 ——
     * 不然每续跑一次就多出一批重复素材（比没跑完更糟）。
     */
    private void registerCutSegments(long jobId, Path outDir, JsonNode plan, List<Integer> cutIdx) {
        ObjectNode result = mapper.createObjectNode();
        try {
            Long sourceAssetId = store.getJob(jobId).map(Store.JobRow::inputAssetId).orElse(null);
            String srcFile = store.getJob(jobId).map(Store.JobRow::inputPath).orElse("");
            String base = Path.of(srcFile).getFileName().toString().replaceFirst("\\.[^.]+$", "");
            String sourceName = assets.get(sourceAssetId == null ? 0 : sourceAssetId)
                    .map(Store.AssetRow::name).filter(n -> n != null && !n.isBlank()).orElse(base);
            result.put("source_asset_id", sourceAssetId == null ? 0 : sourceAssetId);
            ArrayNode items = result.putArray("registered");
            Path marker = outDir.resolve("work").resolve("registered.json");
            if (Files.isRegularFile(marker)) {
                // 续跑：上次已登记过，**绝不再建一遍**
                JsonNode prev = mapper.readTree(Files.readString(marker, StandardCharsets.UTF_8));
                prev.path("registered").forEach(items::add);
                result.put("resumed", true);
                log.info("job {} 续跑：登记记录已存在，复用 {} 条", jobId, items.size());
            } else {
                for (JsonNode seg : plan.path("segments")) {
                    int idx = seg.path("index").asInt();
                    if (!cutIdx.contains(idx)) {
                        continue;
                    }
                    Path file = Path.of(seg.path("file").asText(""));
                    if (!Files.isRegularFile(file)) {
                        log.warn("job {} 第 {} 段的文件不存在，跳过登记：{}", jobId, idx, file);
                        continue;
                    }
                    String title = sourceName + " 第" + idx + "段（"
                            + String.format(java.util.Locale.ROOT, "%.2f", seg.path("start").asDouble())
                            + "–"
                            + String.format(java.util.Locale.ROOT, "%.2f", seg.path("end").asDouble())
                            + "s）";
                    String rel = rel(file);
                    long assetId = assets.register(rel, "generated", title, "场景切段");
                    if (sourceAssetId != null) {
                        assets.link(assetId, "asset", sourceAssetId);
                    }
                    ObjectNode one = items.addObject();
                    one.put("index", idx);
                    one.put("asset_id", assetId);
                    one.put("file", rel);
                }
                Files.writeString(marker, result.toString(), StandardCharsets.UTF_8);
            }
        } catch (Exception e) {                              // noqa: BLE001
            log.warn("job {} 切段产物登记失败（任务本身仍算成功）：{}", jobId, e.getMessage());
            result.put("register_error", String.valueOf(e.getMessage()));
        }
        result.put("count", result.path("registered").size());
        result.set("cut", mapper.valueToTree(cutIdx));
        store.finishJobWithResult(jobId, Store.JOB_SUCCEEDED, null, result.toString());
        log.info("job {} 切段完成：{} 段已入素材库", jobId, result.path("count").asInt());
    }

    // ---------------- 命令 ----------------

    /** CLI 命令。方案阶段加 --dry-run；切段阶段带 --pick（**必带**，不允许隐式全切）。 */
    private List<String> buildCommand(boolean cut, String inputPath, Path outDir,
                                      Map<String, Object> params) {
        List<String> cmd = new ArrayList<>(List.of(resolvePython(), resolveScript(), inputPath));
        if (!cut) {
            cmd.add("--dry-run");
        }
        cmd.addAll(List.of("--yes", "--level", "normal",
                "--events", outDir.resolve(cut ? "events-cut.jsonl" : "events.jsonl").toString(),
                "--workdir", outDir.resolve("work").toString(),
                "-o", outDir.toString()));
        putIf(cmd, params, "threshold", "--threshold");
        putIf(cmd, params, "min_sec", "--min-sec");
        putIf(cmd, params, "max_snap", "--max-snap");
        if (Boolean.TRUE.equals(params.get("no_snap"))) {
            cmd.add("--no-snap");
        }
        if (Boolean.TRUE.equals(params.get("no_thumb"))) {
            cmd.add("--no-thumb");
        }
        if (cut) {
            cmd.add("--pick");
            cmd.add(joinPick(params.get("pick")));
        }
        return cmd;
    }

    private static String joinPick(Object pick) {
        if (pick instanceof List<?> l) {
            return l.stream().map(String::valueOf)
                    .collect(java.util.stream.Collectors.joining(","));
        }
        return String.valueOf(pick);
    }

    private static void putIf(List<String> cmd, Map<String, Object> params, String key, String flag) {
        Object v = params.get(key);
        if (v != null && !String.valueOf(v).isBlank()) {
            cmd.add(flag);
            cmd.add(String.valueOf(v));
        }
    }

    // ---------------- 工具 ----------------

    /** venv 的 python 在不同平台位置不同：Windows 是 Scripts/，POSIX 是 bin/。 */
    private String resolvePython() {
        Path p = Path.of(python);
        if (Files.isRegularFile(p)) {
            return p.toString();
        }
        Path alt = p.getParent() != null && "Scripts".equals(String.valueOf(p.getParent().getFileName()))
                ? p.getParent().getParent().resolve("bin").resolve(p.getFileName())
                : p;
        return Files.isRegularFile(alt) ? alt.toString() : python;
    }

    /** 切段脚本与粗剪脚本**同目录** —— 跟着配置走，不写死路径。 */
    private String resolveScript() {
        Path parent = Path.of(script).getParent();
        return (parent == null ? Path.of("scripts") : parent).resolve("scene_split.py").toString();
    }

    private String rel(Path p) {
        return mediaDir.root().relativize(p.toAbsolutePath().normalize()).toString().replace('\\', '/');
    }

    /**
     * 读子进程日志，**宽松解码**（ffmpeg 在 Windows 上写的是控制台代码页，
     * 严格 UTF-8 解码会抛异常 —— 见 RoughcutRunner 同名方法的实测记录）。
     */
    private static String readLogLenient(Path f) {
        try {
            return Files.isRegularFile(f)
                    ? new String(Files.readAllBytes(f), StandardCharsets.UTF_8) : "";
        } catch (IOException e) {
            return "";
        }
    }

    private static String tail(String s, int max) {
        if (s == null) {
            return "";
        }
        String t = s.strip();
        return t.length() <= max ? t : t.substring(t.length() - max);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> fromJson(String s) {
        try {
            return new ObjectMapper().readValue(s == null ? "{}" : s, Map.class);
        } catch (Exception e) {                              // noqa: BLE001
            return Map.of();
        }
    }

    private static String toJson(Map<String, Object> params) {
        try {
            return new ObjectMapper().writeValueAsString(params);
        } catch (Exception e) {
            return "{}";
        }
    }
}
