package com.polyface.backend.editor;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 受管的 {@code gimpish serve} 长驻子进程（M6-2，见 {@code docs/68} §3）。
 *
 * <p>职责边界：**进程归 Java 管**（Java 是编排者与生命周期所有者），Python 侧不碰它。
 * 本类只做「定位 → 懒启动 → 健康检查 → 复用/重启 → 退出清理」，不做任何场景改写。
 *
 * <p>降级不变量：工具缺失 / 端口被占 / 启动超时一律返回 {@code needs_manual} 结果，
 * **不是异常** —— 与 M6-1（{@code docs/64} §3）同一策略：能力缺失而非调用失败。
 */
@Component
public class EditorProcess {

    private static final Logger log = LoggerFactory.getLogger(EditorProcess.class);

    /** 只用于读 `package.json` 里的版本号（见 {@link #versionFromPackageJson}）。 */
    private static final ObjectMapper MAPPER = new ObjectMapper();

    /**
     * 安装指引。刻意与 Python 侧 {@code app/pipeline/cover.py} 的文案保持一致：
     * 同一件事在 M6-1（CLI）与 M6-2（serve）给用户的应是同一条出路。
     */
    static final String HINT_GIMPISH_MISSING =
            "未检测到图像编辑器 gimpish。请安装 Node.js ≥ 20.19 后执行 "
                    + "`npm install -g gimpish`，或用环境变量 POLYFACE_GIMPISH 指向其可执行文件/入口 js。";

    static final String HINT_DISABLED =
            "编辑器已在配置中关闭（polyface.editor.enabled=false），未尝试启动 serve。";

    /** PATH 与「配置指向目录」两种情形下的候选名（顺序即优先级，见 docs/68 §3）。 */
    private static final String[] ENTRY_CANDIDATES = {"gimpish.cmd", "gimpish", "gimpish.js"};

    /** stdout/stderr 环形缓冲容量：只为 hint 里能附一段现场，不做日志系统。 */
    private static final int RING_MAX_LINES = 100;

    /** hint 引用进程输出的截断长度（与 docs/64 §3 的 stderr 400 字符同口径）。 */
    private static final int HINT_TAIL_CHARS = 400;

    /** 健康检查总预算（docs/68 §3：超时 15s → 杀进程 + needs_manual）。 */
    private static final int HEALTH_TIMEOUT_MS = 15_000;

    /** 健康检查轮询间隔。node 冷启动实测数百毫秒，250ms 既能早返回又不空转。 */
    private static final int HEALTH_POLL_MS = 250;

    /** 停旧进程时等它退出的上限（docs/68 §3：destroy + 等端口释放，最多 5s）。 */
    private static final int STOP_WAIT_MS = 5_000;

    private final String gimpishPathConfig;
    private final int port;
    private final boolean enabled;

    /** {@code open} 的互斥锁：两个请求同时到，绝不允许起两个 serve（docs/68 §3 并发）。 */
    private final Object lock = new Object();

    private final Deque<String> ring = new ArrayDeque<>();

    // ---------------- 运行态（status 会被其它线程读，故 volatile） ----------------

    private volatile ManagedProcess process;
    private volatile Path sceneDir;
    /** 探测到的 gimpish 版本；{@code null} = 尚未探测（探测失败则落成空串并缓存）。 */
    private volatile String version;

    // ---------------- 测试缝（package-private，刻意不做成对外 API） ----------------

    /** 受管子进程的最小抽象：只暴露编排需要的能力，便于单测用替身而**不起真进程**。 */
    interface ManagedProcess {
        boolean isAlive();

        InputStream stdout();

        InputStream stderr();

        void destroy();

        void destroyForcibly();

        /** 先杀后代：Windows 上 cmd 包装器会留下 node 孤儿，不杀则端口不释放。 */
        void destroyDescendants();
    }

    @FunctionalInterface
    interface Launcher {
        ManagedProcess launch(List<String> cmd, Path cwd) throws IOException;
    }

    @FunctionalInterface
    interface Prober {
        boolean healthy(int port);
    }

    private Launcher launcher = this::defaultLaunch;
    private Prober prober = this::probeScene;
    private Supplier<String> versionDetector = this::detectVersion;
    private int healthTimeoutMs = HEALTH_TIMEOUT_MS;
    private int probeTimeoutMs = 2_000;

    public EditorProcess(@Value("${polyface.gimpish.path:}") String gimpishPath,
                         @Value("${polyface.editor.port:8765}") int port,
                         @Value("${polyface.editor.enabled:true}") boolean enabled) {
        this.gimpishPathConfig = gimpishPath;
        this.port = port;
        this.enabled = enabled;
        try {
            Runtime.getRuntime().addShutdownHook(new Thread(this::shutdownQuietly, "gimpish-shutdown"));
        } catch (IllegalStateException e) {
            // JVM 已在退出流程中（单测多次 new 也会走到这里）：没有钩子可挂，属正常
        }
    }

    // ---------------- 对外（供 EditorController） ----------------

    public record Status(boolean available, boolean running, String url, int port,
                         String scene, String version, String hint) {
    }

    public record OpenResult(String status, String url, int port, String scene,
                             String version, String hint, long elapsedMs) {
    }

    /** 是否找得到 gimpish（与「有没有起进程」无关）。 */
    public boolean available() {
        return resolveExecutable() != null;
    }

    /** serve 进程活着**且**健康检查通过（docs/68 §2.1 对 running 的定义）。 */
    public boolean isRunning() {
        ManagedProcess p = process;
        return enabled && p != null && p.isAlive() && prober.healthy(port);
    }

    public Status status() {
        boolean available = available();
        boolean running = available && isRunning();
        String hint = "";
        if (!enabled) {
            hint = HINT_DISABLED;
        } else if (!available) {
            hint = HINT_GIMPISH_MISSING;
        } else if (process != null && !running) {
            hint = "编辑器进程无响应，可先调用 stop 再 open 重试。";
        }
        return new Status(
                available,
                running,
                running ? baseUrl() : "",
                port,
                running && sceneDir != null ? sceneDir.toString() : "",
                // 只有确实装了才探测版本：没装时去跑 `--version` 是白费一次 spawn
                available ? versionOrEmpty() : "",
                hint);
    }

    /**
     * 让 serve 指向 {@code targetDir}：同目录且健康 → 复用；否则停旧起新。
     * 整个过程持 {@link #lock}，避免并发 open 起两个进程。
     */
    public OpenResult open(Path targetDir) {
        long t0 = System.nanoTime();
        Path dir = targetDir.toAbsolutePath().normalize();
        synchronized (lock) {
            if (!enabled) {
                return degrade(dir, HINT_DISABLED, t0);
            }
            String exe = resolveExecutable();
            if (exe == null) {
                return degrade(dir, HINT_GIMPISH_MISSING, t0);
            }
            // 复用：同目录且健康就不重启 —— 重启会让用户正开着的编辑页面白屏
            if (dir.equals(sceneDir) && isRunning()) {
                return ok(dir, t0);
            }
            if (process != null) {
                stopInternal();
            }
            if (start(exe, dir)) {
                return ok(dir, t0);
            }
            return degrade(dir, startFailureHint(), t0);
        }
    }

    /** 手动停：幂等，没在跑返回 false（docs/68 §2.3）。 */
    public boolean stop() {
        synchronized (lock) {
            if (process == null) {
                return false;
            }
            stopInternal();
            return true;
        }
    }

    // ---------------- 定位 ----------------

    /**
     * 定位 gimpish 入口：{@code polyface.gimpish.path} > 环境变量 {@code POLYFACE_GIMPISH} > PATH。
     *
     * <p>前两者一旦给了值就是**权威**：指错了不悄悄回退 PATH。否则用户明明写错路径却"看起来能用"，
     * 排查成本极高；也让单测可以确定地构造"未安装"。
     */
    private String resolveExecutable() {
        String configured = firstNonBlank(gimpishPathConfig, System.getenv("POLYFACE_GIMPISH"));
        if (configured != null) {
            return findUnder(Path.of(configured));
        }
        return findOnPath();
    }

    /** 配置/环境变量可以指向入口文件本身，也可以指向装着入口的目录（npm 全局 bin）。 */
    private static String findUnder(Path base) {
        if (Files.isRegularFile(base)) {
            return base.toString();
        }
        if (Files.isDirectory(base)) {
            for (String name : ENTRY_CANDIDATES) {
                Path cand = base.resolve(name);
                if (Files.isRegularFile(cand)) {
                    return cand.toString();
                }
            }
        }
        return null;
    }

    /** PATH 查找：不依赖 OS 的 which/PATHEXT（Windows 上 npm 会落 {@code gimpish.cmd}）。 */
    private static String findOnPath() {
        String path = System.getenv("PATH");
        if (path == null || path.isBlank()) {
            return null;
        }
        for (String dir : path.split(Pattern.quote(File.pathSeparator))) {
            if (dir.isBlank()) {
                continue;
            }
            for (String name : ENTRY_CANDIDATES) {
                Path cand = Path.of(dir, name);
                if (Files.isRegularFile(cand)) {
                    return cand.toString();
                }
            }
        }
        return null;
    }

    private static String firstNonBlank(String a, String b) {
        if (a != null && !a.isBlank()) {
            return a.trim();
        }
        return (b != null && !b.isBlank()) ? b.trim() : null;
    }

    // ---------------- 启动 / 停止 ----------------

    private ManagedProcess defaultLaunch(List<String> cmd, Path cwd) throws IOException {
        ProcessBuilder pb = new ProcessBuilder(cmd).directory(cwd.toFile());
        log.info("启动 gimpish serve: {} (cwd={})", cmd, cwd);
        return new JdkManagedProcess(pb.start());
    }

    /**
     * 起进程并等健康检查通过。失败返回 false（调用方转 needs_manual），**不抛异常**。
     * 端口被占的情形也走这条路：serve 绑定失败会自己退出，轮询里 {@code !isAlive()} 即判定失败。
     */
    private boolean start(String exe, Path dir) {
        ManagedProcess p;
        try {
            p = launcher.launch(commandFor(exe, dir), dir);
        } catch (Exception e) {
            log.warn("gimpish serve 启动失败: {}", e.toString());
            appendRing("[launch failed] " + e);
            return false;
        }
        process = p;
        pump(p.stdout(), "out");
        pump(p.stderr(), "err");
        if (awaitHealthy(p)) {
            sceneDir = dir;
            version = versionOrEmpty();
            log.info("gimpish serve 就绪: port={} scene={}", port, dir);
            return true;
        }
        appendRing("[health] 端口 " + port + " 在 " + healthTimeoutMs + "ms 内未通过 /api/scene");
        process = null;
        kill(p);
        return false;
    }

    private void stopInternal() {
        ManagedProcess p = process;
        process = null;
        sceneDir = null;
        if (p == null) {
            return;
        }
        try {
            p.destroyDescendants();
            p.destroy();
            // 等它真的退出（等价于端口释放）。不等就起新进程，会和没退干净的旧进程抢端口
            long deadline = System.currentTimeMillis() + STOP_WAIT_MS;
            while (p.isAlive() && System.currentTimeMillis() < deadline) {
                Thread.sleep(100);
            }
            if (p.isAlive()) {
                p.destroyForcibly();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            log.warn("停止 gimpish serve 出错: {}", e.toString());
        }
    }

    private void kill(ManagedProcess p) {
        try {
            p.destroyDescendants();
            p.destroyForcibly();
        } catch (Exception e) {
            log.warn("清理 gimpish serve 出错: {}", e.toString());
        }
    }

    private void shutdownQuietly() {
        ManagedProcess p = process;
        if (p != null) {
            kill(p);
        }
    }

    private List<String> commandFor(String exe, Path dir) {
        List<String> cmd = new ArrayList<>();
        if (exe.toLowerCase(Locale.ROOT).endsWith(".js")) {
            // .js 入口不是可执行文件，必须显式交给 node（docs/68 §3）
            cmd.add(findNode());
        }
        cmd.add(exe);
        cmd.add("serve");
        cmd.add("--port");
        cmd.add(String.valueOf(port));
        cmd.add("--scene");
        cmd.add(dir.toString());
        return cmd;
    }

    private static String findNode() {
        String path = System.getenv("PATH");
        if (path != null) {
            for (String dir : path.split(Pattern.quote(File.pathSeparator))) {
                if (dir.isBlank()) {
                    continue;
                }
                for (String name : new String[]{"node.exe", "node"}) {
                    Path cand = Path.of(dir, name);
                    if (Files.isRegularFile(cand)) {
                        return cand.toString();
                    }
                }
            }
        }
        return "node"; // 交给 OS 解析；真起不来会走降级路径，而不是在这里炸掉
    }

    // ---------------- 健康检查 ----------------

    private boolean awaitHealthy(ManagedProcess p) {
        long deadline = System.currentTimeMillis() + healthTimeoutMs;
        while (System.currentTimeMillis() < deadline) {
            if (!p.isAlive()) {
                return false; // 进程自己退了（端口被占 / 参数错），再轮询没有意义
            }
            if (prober.healthy(port)) {
                return true;
            }
            try {
                Thread.sleep(HEALTH_POLL_MS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    /**
     * 打 {@code /api/scene} 而不是 {@code /}：前者能证明**场景已加载**，后者只能证明 HTTP 起来了
     * （docs/68 §3）。场景没加载时内嵌 iframe 会白屏，比启动慢更糟。
     *
     * <p>强制 HTTP/1.1：gimpish 是 Node http，不做 h2c 明文升级（同 {@code PythonClient} 的处置）。
     */
    private boolean probeScene(int port) {
        try {
            HttpClient client = HttpClient.newBuilder()
                    .version(HttpClient.Version.HTTP_1_1)
                    .connectTimeout(Duration.ofMillis(probeTimeoutMs))
                    .build();
            HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl(port) + "/api/scene"))
                    .timeout(Duration.ofMillis(probeTimeoutMs))
                    .GET()
                    .build();
            return client.send(req, HttpResponse.BodyHandlers.discarding()).statusCode() == 200;
        } catch (Exception e) {
            return false; // 连不上/超时/非 200 都只是"还没就绪"
        }
    }

    // ---------------- 版本 ----------------

    /** 探测不到就返回空串（docs/68 §2.1：version 允许为空，不该为此报错）。 */
    private String versionOrEmpty() {
        String v = version;
        if (v == null) {
            v = versionDetector.get();
            v = (v == null) ? "" : v;
            version = v;
        }
        return v;
    }

    private String detectVersion() {
        String exe = resolveExecutable();
        if (exe == null) {
            return "";
        }
        // 主路径：读 node_modules/gimpish/package.json —— 实测 0.1.0 **没有** `--version`
        // 选项（会打印 unknown option），只靠子进程探测会永远拿到空串。
        // 口径与 scripts/doctor.py::gimpish_version 保持一致，免得两处报不同的版本。
        String fromPkg = versionFromPackageJson(Path.of(exe));
        if (!fromPkg.isEmpty()) {
            return fromPkg;
        }
        // 兜底：将来若加了 `--version`，也能认出来
        List<String> cmd = new ArrayList<>();
        if (exe.toLowerCase(Locale.ROOT).endsWith(".js")) {
            cmd.add(findNode());
        }
        cmd.add(exe);
        cmd.add("--version");
        try {
            Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
            if (!p.waitFor(5, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                return "";
            }
            String out = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
            Matcher m = Pattern.compile("(\\d+\\.\\d+\\.\\d+)").matcher(out);
            return m.find() ? m.group(1) : "";
        } catch (Exception e) {
            log.debug("gimpish 版本探测失败: {}", e.toString());
            return "";
        }
    }

    /**
     * 从入口所在路径向上找名为 {@code gimpish} 的包目录并读其 {@code package.json} 版本。
     * 找不到（例如用户指向一个自定义包装脚本）返回空串 —— 版本是**信息性字段**，不阻塞任何流程。
     */
    static String versionFromPackageJson(Path exe) {
        for (Path p = exe.toAbsolutePath().normalize(); p != null; p = p.getParent()) {
            if (!"gimpish".equals(String.valueOf(p.getFileName()))) {
                continue;
            }
            Path pkg = p.resolve("package.json");
            if (!Files.isRegularFile(pkg)) {
                return "";
            }
            try {
                return MAPPER.readTree(Files.readString(pkg, StandardCharsets.UTF_8))
                        .path("version").asText("");
            } catch (Exception e) {
                return "";
            }
        }
        return "";
    }

    // ---------------- 输出环形缓冲 ----------------

    private void pump(InputStream in, String tag) {
        Thread t = new Thread(() -> {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) {
                    appendRing("[" + tag + "] " + line);
                }
            } catch (IOException ignored) {
                // 进程退出时流被关闭，属正常收尾
            }
        }, "gimpish-" + tag);
        t.setDaemon(true);
        t.start();
    }

    private void appendRing(String line) {
        synchronized (ring) {
            ring.addLast(line);
            while (ring.size() > RING_MAX_LINES) {
                ring.removeFirst();
            }
        }
    }

    /** 最近若干行输出，截到 {@value #HINT_TAIL_CHARS} 字符，供 hint 附现场。 */
    private String ringTail() {
        synchronized (ring) {
            if (ring.isEmpty()) {
                return "";
            }
            StringBuilder sb = new StringBuilder();
            for (String line : ring) {
                sb.append(line).append('\n');
                if (sb.length() > HINT_TAIL_CHARS) {
                    break;
                }
            }
            String s = sb.toString().trim();
            return s.length() > HINT_TAIL_CHARS ? s.substring(s.length() - HINT_TAIL_CHARS) : s;
        }
    }

    // ---------------- 结果组装 ----------------

    private OpenResult ok(Path dir, long t0) {
        return new OpenResult("ok", baseUrl(), port, dir.toString(), versionOrEmpty(), "", elapsedMs(t0));
    }

    private OpenResult degrade(Path dir, String hint, long t0) {
        // 用已缓存版本（可能是 null → 空串），**不**为降级路径额外 spawn 一次探测
        String v = version == null ? "" : version;
        return new OpenResult("needs_manual", "", port, dir.toString(), v, hint, elapsedMs(t0));
    }

    private String startFailureHint() {
        String tail = ringTail();
        String base = "gimpish serve 未能在 " + (healthTimeoutMs / 1000) + "s 内就绪"
                + "（端口 " + port + " 可能被占用，或场景目录不可用）。";
        return tail.isEmpty() ? base : base + " 进程输出：" + tail;
    }

    private String baseUrl() {
        return baseUrl(port);
    }

    private static String baseUrl(int port) {
        // 只监听回环：与 polyface 现有姿态一致，不新增对外暴露面（docs/68 §5）
        return "http://127.0.0.1:" + port;
    }

    private static long elapsedMs(long t0) {
        return (System.nanoTime() - t0) / 1_000_000;
    }

    // ---------------- 测试缝注入（package-private） ----------------

    void setLauncher(Launcher launcher) {
        this.launcher = launcher;
    }

    void setProber(Prober prober) {
        this.prober = prober;
    }

    void setVersionDetector(Supplier<String> versionDetector) {
        this.versionDetector = versionDetector;
    }

    void setHealthTimeoutMs(int healthTimeoutMs) {
        this.healthTimeoutMs = healthTimeoutMs;
    }

    void setProbeTimeoutMs(int probeTimeoutMs) {
        this.probeTimeoutMs = probeTimeoutMs;
    }

    /** 真实子进程的适配器。 */
    private static final class JdkManagedProcess implements ManagedProcess {

        private final Process p;

        JdkManagedProcess(Process p) {
            this.p = p;
        }

        @Override
        public boolean isAlive() {
            return p.isAlive();
        }

        @Override
        public InputStream stdout() {
            return p.getInputStream();
        }

        @Override
        public InputStream stderr() {
            return p.getErrorStream();
        }

        @Override
        public void destroy() {
            p.destroy();
        }

        @Override
        public void destroyForcibly() {
            p.destroyForcibly();
        }

        @Override
        public void destroyDescendants() {
            p.toHandle().descendants().forEach(ProcessHandle::destroyForcibly);
        }
    }
}
