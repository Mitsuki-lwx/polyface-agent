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

import com.polyface.backend.asset.AssetService;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 跑一次粗剪任务（M8 第一片）。
 *
 * <p><b>选型</b>（见 `docs/spec_roughcut_jobs.md` §5）：Java **起子进程跑 CLI**，
 * 两者之间用**阶段事件文件**当契约 —— 不新开 Python 的 job API。
 * 好处是 CLI 保持是**唯一实现**，不会出现"命令行一套逻辑、服务里另一套"的分叉。
 *
 * <p>进程管理的写法照抄 {@code editor.EditorProcess}（那套已经在跑 gimpish serve）：
 * 有界线程池、杀**进程树**、退出时清理。
 */
@Component
public class RoughcutRunner {

    private static final Logger log = LoggerFactory.getLogger(RoughcutRunner.class);

    private final Store store;
    private final MediaDir mediaDir;
    private final AssetService assets;

    private final String python;
    private final String script;
    private final int stallSec;
    private final int keepJobs;

    /** 单线程：单机单用户，且视频转码本来就吃满 CPU（spec §1 默认值表）。 */
    private final ExecutorService pool = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "roughcut-job");
        t.setDaemon(true);
        return t;
    });

    private final Map<Long, Process> running = new ConcurrentHashMap<>();

    public RoughcutRunner(Store store, MediaDir mediaDir, AssetService assets,
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
        recoverInterrupted();
    }

    /**
     * 上次没跑完的任务：进程早没了，状态却还停在"运行中" —— 重启时必须收拾干净。
     *
     * <p>不这么做的话，工作台会永远显示一个**永远不会动的进度条**
     * （checklist §3 明确要求"进程意外死亡能被识别"）。
     *
     * <p>但**"收拾干净"不等于"判死刑"**：标为 {@link Store#JOB_INTERRUPTED} 而不是
     * {@code failed} —— 粗剪的中间产物本来就落盘保留，用户点一下就能从断掉的阶段接着跑。
     * 见 `docs/spec_resume.md`。
     */
    private void recoverInterrupted() {
        try {
            List<Store.JobRow> inFlight = store.jobsInFlight();
            for (Store.JobRow j : inFlight) {
                store.markJobInterrupted(j.id());
            }
            if (!inFlight.isEmpty()) {
                log.warn("启动清理：{} 个任务在上次运行中未结束，已标为「可继续」", inFlight.size());
            }
        } catch (Exception e) {
            log.warn("启动清理未完成：{}", e.getMessage());
        }
    }

    // ---------------- 提交 ----------------

    public static final String KIND_ROUGHCUT = "roughcut";
    public static final String KIND_RENDER_TIMELINE = "render_timeline";
    /** 图文成片（`docs/spec_b1.md`）—— 输入是一份**成片计划**（分镜 + 图 + BGM）。 */
    public static final String KIND_MAKE_VIDEO = "make_video";

    /** 提交一个粗剪任务。返回 jobId（状态 `queued`）。 */
    public long submit(Long inputAssetId, String inputPath, Map<String, Object> params, String baseName) {
        return submit(KIND_ROUGHCUT, inputAssetId, inputPath, params, baseName);
    }

    /**
     * 从时间线渲染成片（`docs/spec_timeline.md`）：输入是**时间线文件**，产出新成片。
     *
     * <p>为什么它必须走任务：它是**分钟级**的（要从源视频重剪 + 烧字幕），
     * 必须能看进度、能取消、能断点续跑 —— 这些 M8 已经全有了，复用它。
     */
    public long submitRender(long timelineAssetId, String timelinePath, Map<String, Object> params) {
        return submit(KIND_RENDER_TIMELINE, timelineAssetId, timelinePath, params, "render");
    }

    /**
     * 图文成片（`docs/spec_b1.md` T7）：输入是**成片计划**的内容。
     *
     * <p>计划由 Java 侧从"成稿的剪辑单 + 用户选的图/BGM"拼出来（Python 不读库），
     * 写进任务目录后再跑 —— 这样计划本身也成了可复核的产物。
     */
    public long submitMakeVideo(String planJson, Map<String, Object> params) {
        Path outDir = mediaDir.root().resolve("roughcut").resolve("job-" + System.nanoTime());
        try {
            Files.createDirectories(outDir);
            Path plan = outDir.resolve("plan.json");
            Files.writeString(plan, planJson, java.nio.charset.StandardCharsets.UTF_8);
            String events = outDir.resolve("events.jsonl").toString();
            long jobId = store.insertJob(KIND_MAKE_VIDEO, toJson(params), null,
                    plan.toString(), outDir.toString(), events);
            store.pruneJobs(keepJobs);
            pool.submit(() -> execute(jobId, KIND_MAKE_VIDEO, plan.toString(), outDir,
                    "film", params));
            return jobId;
        } catch (IOException e) {
            throw new IllegalStateException("无法创建成片任务目录：" + e.getMessage(), e);
        }
    }

    private long submit(String kind, Long inputAssetId, String inputPath,
                        Map<String, Object> params, String baseName) {
        Path outDir = mediaDir.root().resolve("roughcut").resolve("job-" + System.nanoTime());
        try {
            Files.createDirectories(outDir);
        } catch (IOException e) {
            throw new IllegalStateException("无法创建任务目录：" + e.getMessage(), e);
        }
        String events = outDir.resolve("events.jsonl").toString();
        String paramsJson = toJson(params);
        long jobId = store.insertJob(kind, paramsJson, inputAssetId, inputPath,
                outDir.toString(), events);
        store.pruneJobs(keepJobs);
        pool.submit(() -> execute(jobId, kind, inputPath, outDir, baseName, params));
        return jobId;
    }

    /**
     * 续跑一个「已中断」的粗剪任务：**沿用原来的任务目录**。
     *
     * <p>跳过逻辑在 **Python 侧**（`scripts/roughcut.py` 的 `_Done`）：它读 `work/done/`
     * 里已完成的阶段，按依赖校验后只跑缺的。Java 这边只要把**同样的命令**再发一次 ——
     * 目录不变，判据不变，自然就接上了。
     *
     * @return 是否受理（状态不对时返回 false，由调用方给出可读理由）
     */
    public boolean resume(long jobId) {
        Store.JobRow j = store.getJob(jobId).orElse(null);
        if (j == null || !Store.JOB_INTERRUPTED.equals(j.status())) {
            return false;
        }
        // 基名按 kind 还原（与 submit 时一致）：粗剪是 out，渲染是 render
        String baseName = switch (j.kind()) {
            case KIND_RENDER_TIMELINE -> "render";
            case KIND_MAKE_VIDEO -> "film";
            default -> "out";
        };
        pool.submit(() -> execute(jobId, j.kind(), j.inputPath(), Path.of(j.workdir()),
                baseName, fromJson(j.paramsJson())));
        return true;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> fromJson(String s) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().readValue(s, Map.class);
        } catch (Exception e) {                                  // noqa: BLE001
            return Map.of();
        }
    }

    private void execute(long jobId, String kind, String inputPath, Path outDir,
                         String baseName, Map<String, Object> params) {
        store.markJobRunning(jobId);
        Path out = outDir.resolve(baseName + ".mp4");
        Path srt = outDir.resolve(baseName + ".srt");
        Path cuts = outDir.resolve("work").resolve("cuts.json");
        Path timeline = outDir.resolve(baseName + ".timeline.json");
        Path errFile = outDir.resolve("stderr.log");

        int exit;
        try {
            List<String> cmd = switch (kind) {
                case KIND_RENDER_TIMELINE -> buildRenderCommand(inputPath, out, outDir, params);
                case KIND_MAKE_VIDEO -> buildMakeVideoCommand(inputPath, out, outDir, params);
                default -> buildCommand(inputPath, out, outDir, params);
            };
            log.info("job {} 启动：{}", jobId, String.join(" ", cmd));
            ProcessBuilder pb = new ProcessBuilder(cmd).directory(new java.io.File("."));
            pb.redirectErrorStream(true);
            pb.redirectOutput(errFile.toFile());
            Process p = pb.start();
            running.put(jobId, p);
            exit = p.waitFor();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            // **"被中断"不等于"失败"**：已跑完阶段的落盘都在，可以继续。
            // 标成 failed 会让它从"可继续"里消失 —— 那正是这次要修的东西。
            store.markJobInterrupted(jobId);
            return;
        } catch (IOException e) {
            store.finishJob(jobId, Store.JOB_FAILED, "启动失败：" + e.getMessage(), null, null, null);
            return;
        } finally {
            running.remove(jobId);
        }

        // 读日志**单独一步**：读不出来不该被说成"启动失败"（曾经就误报过）
        String errText = readLogLenient(errFile);

        boolean canceled = Store.JOB_CANCELED.equals(store.getJob(jobId)
                .map(Store.JobRow::status).orElse(""));
        if (canceled) {
            store.finishJob(jobId, Store.JOB_CANCELED, "已取消", null, null, null);
            return;
        }
        if (exit != 0) {
            store.finishJob(jobId, Store.JOB_FAILED, tail(errText, 500), null, null, null);
            log.warn("job {} 失败 exit={}：{}", jobId, exit, tail(errText, 200));
            return;
        }
        // ⚠️ 退出码 0 **不等于**有产出：素材里找不到停顿（例如拿一段已经剪过的视频再剪）
        // 时，CLI 会如实报告并**正常退出**、不产文件。这种"跑完但什么都没有"必须记成失败，
        // 否则工作台上会出现一个"✅ 完成"却没有任何产物的任务（实测踩过）。
        if (!Files.isRegularFile(out)) {
            store.finishJob(jobId, Store.JOB_FAILED,
                    "没有产出成片（通常是素材里没找到足够长的停顿）。" + tail(errText, 300),
                    null, null, null);
            log.warn("job {} 正常退出但没有产出：{}", jobId, tail(errText, 200));
            return;
        }
        // 产物登记进素材库，并挂到源视频上 —— **登记失败不影响任务成功**
        if (KIND_MAKE_VIDEO.equals(kind)) {
            registerFilmOutputs(jobId, out, srt, outDir.resolve("plan.json"));
        } else if (KIND_RENDER_TIMELINE.equals(kind)) {
            registerRenderOutputs(jobId, out, srt);
        } else {
            registerOutputs(jobId, outDir, out, srt, cuts, timeline, params);
        }
        store.finishJob(jobId, Store.JOB_SUCCEEDED, null, out.toString(),
                srt.toString(), cuts.toString());
        log.info("job {} 完成：{}", jobId, out);
    }

    /**
     * 读子进程日志，**宽松解码**。
     *
     * <p>为什么不能直接 `Files.readString(p, UTF_8)`：那是**严格**解码，
     * 遇到非法字节直接抛 `MalformedInputException: Input length = 1` ——
     * 而 ffmpeg 在 Windows 上往 stderr 写的是**控制台代码页**（GBK），不是 UTF-8。
     * 这个异常曾经被误报成"启动失败"，把一次**成功的**任务记成了失败。
     * `new String(bytes, UTF_8)` 默认是 REPLACE，不会抛。
     */
    private static String readLogLenient(Path f) {
        try {
            return Files.isRegularFile(f)
                    ? new String(Files.readAllBytes(f), StandardCharsets.UTF_8) : "";
        } catch (IOException e) {
            return "";
        }
    }

    private List<String> buildCommand(String inputPath, Path out, Path outDir,
                                      Map<String, Object> params) {
        List<String> cmd = new ArrayList<>(List.of(resolvePython(), resolveScript(),
                inputPath, "--yes", "--level", "normal",
                "--events", outDir.resolve("events.jsonl").toString(),
                "--workdir", outDir.resolve("work").toString(),
                "-o", out.toString()));
        // 参数映射：UI 只可能传这几个；没传的用 CLI 自己的默认（Python 侧是唯一来源）
        putIf(cmd, params, "pause_sec", "--pause");
        putIf(cmd, params, "noise_db", "--noise");
        putIf(cmd, params, "keep_margin_sec", "--margin");
        putIf(cmd, params, "font", "--font");
        putIf(cmd, params, "font_size", "--font-size");
        putIf(cmd, params, "margin_v", "--margin-v");
        if (Boolean.FALSE.equals(params.get("burn_subs"))) {
            cmd.add("--no-burn");
        }
        // 去冗余（docs/spec_disfluency.md）：四类各有独立开关，UI 只传"关掉哪个"。
        // 默认**开**（这是新功能的默认行为，写在这里以免漏传）
        if (Boolean.FALSE.equals(params.get("dedup_enabled"))) {
            cmd.add("--no-dedup");
        } else {
            if (Boolean.FALSE.equals(params.get("dedup_filler"))) {
                cmd.add("--no-dedup-filler");
            }
            if (Boolean.FALSE.equals(params.get("dedup_repeat"))) {
                cmd.add("--no-dedup-repeat");
            }
            if (Boolean.FALSE.equals(params.get("dedup_restart"))) {
                cmd.add("--no-dedup-restart");
            }
            if (Boolean.FALSE.equals(params.get("dedup_self_correct"))) {
                cmd.add("--no-dedup-self-correct");
            }
            putIf(cmd, params, "dedup_similarity", "--dedup-similarity");
            putIf(cmd, params, "dedup_min_cut_sec", "--dedup-min-cut");
            // 模型复核：**只有显式打开才传** —— 开了转写文本会出本机（数据边界）
            if (Boolean.TRUE.equals(params.get("dedup_review"))) {
                cmd.add("--dedup-review");
            }
        }
        return cmd;
    }

    /** 渲染命令：输入是**时间线**，输出路径显式给（免得两边各猜一套命名）。 */
    private List<String> buildRenderCommand(String timelinePath, Path out, Path outDir,
                                            Map<String, Object> params) {
        List<String> cmd = new ArrayList<>(List.of(resolvePython(),
                resolveRenderScript(), timelinePath,
                "-o", out.toString(),
                "--level", "normal",
                "--events", outDir.resolve("events.jsonl").toString(),
                "--workdir", outDir.resolve("work").toString()));
        putIf(cmd, params, "font", "--font");
        putIf(cmd, params, "font_size", "--font-size");
        putIf(cmd, params, "text_color", "--text-color");
        putIf(cmd, params, "margin_v", "--margin-v");
        if (Boolean.FALSE.equals(params.get("burn_subs"))) {
            cmd.add("--no-burn");
        }
        return cmd;
    }

    /** 成片命令：输入是**成片计划**，输出路径显式给。 */
    private List<String> buildMakeVideoCommand(String planPath, Path out, Path outDir,
                                               Map<String, Object> params) {
        List<String> cmd = new ArrayList<>(List.of(resolvePython(),
                resolveSiblingScript("make_video.py"), planPath,
                "-o", out.toString(), "--level", "normal",
                "--events", outDir.resolve("events.jsonl").toString(),
                "--workdir", outDir.resolve("work").toString()));
        putIf(cmd, params, "strategy", "--strategy");
        putIf(cmd, params, "plugin", "--plugin");
        putIf(cmd, params, "voice", "--voice");
        putIf(cmd, params, "transition_sec", "--transition");
        putIf(cmd, params, "zoom", "--zoom");
        putIf(cmd, params, "bgm_volume_db", "--bgm-volume");
        putIf(cmd, params, "font", "--font");
        putIf(cmd, params, "font_size", "--font-size");
        putIf(cmd, params, "margin_v", "--margin-v");
        if (Boolean.FALSE.equals(params.get("burn_subs"))) {
            cmd.add("--no-burn");
        }
        return cmd;
    }

    /** 成片产物：成片 + 字幕 + **初剪记录**（人工定稿要看它）。 */
    private void registerFilmOutputs(long jobId, Path out, Path srt, Path plan) {
        try {
            Long srcAssetId = store.getJob(jobId).map(Store.JobRow::inputAssetId).orElse(null);
            String stem = out.getFileName().toString().replace(".mp4", "");
            long outId = assets.register(rel(out), "generated", stem + "（图文成片）", "图文成片");
            if (Files.isRegularFile(srt)) {
                assets.register(rel(srt), "generated", stem + ".srt（成片字幕）", "图文成片");
            }
            if (Files.isRegularFile(plan)) {
                assets.register(rel(plan), "generated", stem + ".plan.json（初剪记录）", "图文成片");
            }
            if (srcAssetId != null) {
                assets.link(outId, "asset", srcAssetId);
            }
        } catch (Exception e) {                              // noqa: BLE001
            log.warn("job {} 成片产物登记失败（任务本身仍算成功）：{}", jobId, e.getMessage());
        }
    }

    /** 渲染产物：成片 + 字幕。**不重登记时间线**（它是输入，不是这次跑出来的）。 */
    private void registerRenderOutputs(long jobId, Path out, Path srt) {
        try {
            Long tlAssetId = store.getJob(jobId).map(Store.JobRow::inputAssetId).orElse(null);
            String stem = out.getFileName().toString().replace(".mp4", "");
            long outId = assets.register(rel(out), "generated", stem + "（按时间线渲染）", "时间线渲染");
            if (Files.isRegularFile(srt)) {
                assets.register(rel(srt), "generated", stem + ".srt（渲染字幕）", "时间线渲染");
            }
            if (tlAssetId != null) {
                assets.link(outId, "asset", tlAssetId);      // 血缘：这条成片是哪条时间线来的
            }
        } catch (Exception e) {                              // noqa: BLE001
            log.warn("job {} 渲染产物登记失败（任务本身仍算成功）：{}", jobId, e.getMessage());
        }
    }

    private static void putIf(List<String> cmd, Map<String, Object> params, String key, String flag) {
        Object v = params.get(key);
        if (v != null && !String.valueOf(v).isBlank()) {
            cmd.add(flag);
            cmd.add(String.valueOf(v));
        }
    }

    /**
     * 产物入素材库 + 关联到源视频。
     *
     * <p>整段包在 try/catch 里：**登记是附加价值，不能因为它挂了就让任务算失败**
     * （沿用 M7 的既有约定）。
     */
    private void registerOutputs(long jobId, Path outDir, Path out, Path srt, Path cuts,
                                 Path timeline, Map<String, Object> params) {
        try {
            Long sourceAssetId = store.getJob(jobId).map(Store.JobRow::inputAssetId).orElse(null);
            String stem = out.getFileName().toString().replace(".mp4", "");
            long outId = assets.register(rel(out), "generated", stem + "（粗剪成片）", "粗剪");
            assets.register(rel(srt), "generated", stem + ".srt（粗剪字幕）", "粗剪");
            if (Files.isRegularFile(cuts)) {
                assets.register(rel(cuts), "generated", stem + "（剪点）", "粗剪");
            }
            // 时间线（docs/spec_timeline.md）：**一等资产**，不是任务中间件 ——
            // 它是"这个视频该怎么剪"的意图，得活得比任务久，用户才能改了再渲染。
            if (Files.isRegularFile(timeline)) {
                long tlId = assets.register(rel(timeline), "generated",
                        stem + "（时间线）", "时间线");
                if (sourceAssetId != null) {
                    assets.link(tlId, "asset", sourceAssetId);   // 血缘挂回源视频
                }
            }
            if (sourceAssetId != null) {
                assets.link(outId, "asset", sourceAssetId);
            }
        } catch (Exception e) {
            log.warn("job {} 产物登记失败（任务本身仍算成功）：{}", jobId, e.getMessage());
        }
    }

    private String rel(Path p) {
        return mediaDir.root().relativize(p.toAbsolutePath().normalize()).toString().replace('\\', '/');
    }

    // ---------------- 查询与取消 ----------------

    public Optional<JobProgress> progress(Store.JobRow job) {
        boolean runningNow = Store.JOB_RUNNING.equals(job.status());
        return Optional.of(JobProgress.read(
                job.eventsPath() == null ? null : Path.of(job.eventsPath()), runningNow, stallSec));
    }

    /** 取消：**杀进程树**（Windows 上 cmd 包装器会留孤儿，只杀父进程不够）。 */
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

    // ---------------- 路径与工具 ----------------

    /** venv 的 python 在不同平台位置不同：Windows 是 `Scripts/`，POSIX 是 `bin/`。 */
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

    private String resolveScript() {
        return Files.isRegularFile(Path.of(script)) ? script : script;
    }

    /** 渲染脚本与粗剪脚本**同目录** —— 跟着配置走，不写死路径。 */
    private String resolveRenderScript() {
        return resolveSiblingScript("render_timeline.py");
    }

    /** 与本类的主脚本同目录的另一个脚本。跟着配置走，不写死路径。 */
    private String resolveSiblingScript(String name) {
        Path parent = Path.of(resolveScript()).getParent();
        return (parent == null ? Path.of("scripts") : parent).resolve(name).toString();
    }

    private static String toJson(Map<String, Object> params) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(params);
        } catch (Exception e) {
            return "{}";
        }
    }

    private static String tail(String s, int max) {
        if (s == null) {
            return "";
        }
        String t = s.strip();
        return t.length() <= max ? t : t.substring(t.length() - max);
    }

    /** 供配置自检：把最终生效的路径暴露出来（"启用了"要有可查询证据）。 */
    public Map<String, Object> describe() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("python", resolvePython());
        m.put("script", resolveScript());
        m.put("stall_sec", stallSec);
        m.put("keep_jobs", keepJobs);
        m.put("running", new ArrayList<>(running.keySet()));
        return m;
    }

    /**
     * 粗剪参数的**默认值**，直接从 CLI 取（`--print-defaults`）。
     *
     * <p>为什么绕这一圈：默认值的**唯一来源应该是 Python 侧的 `RoughcutParams`**。
     * 在 Java 里再抄一份迟早会漂移 —— 那正是本项目反复吃亏的"两套实现"。
     * 结果缓存：同一进程里默认值不会变（配置文件改了要重启，与 CLI 行为一致）。
     */
    public com.fasterxml.jackson.databind.JsonNode defaults() {
        if (defaultsCache != null) {
            return defaultsCache;
        }
        try {
            Process p = new ProcessBuilder(resolvePython(), resolveScript(), "--print-defaults")
                    .redirectErrorStream(true).start();
            String out = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            if (!p.waitFor(30, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                throw new IOException("--print-defaults 超时");
            }
            defaultsCache = new com.fasterxml.jackson.databind.ObjectMapper().readTree(out);
        } catch (Exception e) {
            log.warn("取粗剪默认参数失败（前端会拿到空默认值）：{}", e.getMessage());
            defaultsCache = new com.fasterxml.jackson.databind.ObjectMapper().createObjectNode();
        }
        return defaultsCache;
    }

    private volatile com.fasterxml.jackson.databind.JsonNode defaultsCache;
}
