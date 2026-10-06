package com.polyface.backend.editor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.sun.net.httpserver.HttpServer;

/**
 * {@link EditorProcess} 的编排逻辑单测：健康检查成功/超时、复用与重启、降级不 spawn。
 *
 * <p>用「launcher 替身 + 真实 HTTP 探测」组合：进程是假的（**不在单测里起真 node**），
 * 但探测走真的 {@code GET /api/scene} 打本地 {@link HttpServer} stub —— 这样验证的正是
 * 生产路径上的探测代码，而不是替身的返回值。
 */
class EditorProcessTest {

    @TempDir
    Path tmp;

    private HttpServer server;
    private final CountDownLatch release = new CountDownLatch(1);

    @AfterEach
    void tearDown() {
        release.countDown();
        if (server != null) {
            server.stop(0);
        }
    }

    /** ① 探到 200 → 启动成功，返回可用的 url/scene/version。 */
    @Test
    void probeSuccessStartsServe() throws Exception {
        int port = stub(true);
        FakeLauncher launcher = new FakeLauncher();
        EditorProcess ep = newEditor(port, true);
        ep.setLauncher(launcher);
        ep.setVersionDetector(() -> "0.1.0");
        ep.setProbeTimeoutMs(500);
        ep.setHealthTimeoutMs(3_000);

        Path dir = tmp.resolve("scene-a");
        EditorProcess.OpenResult r = ep.open(dir);

        assertEquals("ok", r.status());
        assertEquals("http://127.0.0.1:" + port, r.url());
        assertEquals(port, r.port());
        assertEquals(dir.toAbsolutePath().normalize().toString(), r.scene());
        assertEquals("0.1.0", r.version());
        assertEquals("", r.hint());
        assertTrue(ep.isRunning());

        // 命令契约：`<exe> serve --port <n> --scene <dir>`；.js 入口必须前置 node
        assertEquals(1, launcher.commands.size());
        List<String> cmd = launcher.commands.get(0);
        assertTrue(cmd.get(0).endsWith("node") || cmd.get(0).endsWith("node.exe"), "应为 node " + cmd);
        assertTrue(cmd.contains("serve"));
        assertTrue(cmd.contains("--port"));
        assertTrue(cmd.contains(String.valueOf(port)));
        assertTrue(cmd.contains("--scene"));
        assertTrue(cmd.contains(dir.toAbsolutePath().normalize().toString()));
    }

    /** ② 一直不返回（stub 挂住）→ 到点降级 needs_manual，并把子进程杀干净。 */
    @Test
    void probeNeverReadyDegradesAfterTimeout() throws Exception {
        int port = stub(false);
        FakeLauncher launcher = new FakeLauncher();
        EditorProcess ep = newEditor(port, true);
        ep.setLauncher(launcher);
        ep.setProbeTimeoutMs(250);
        ep.setHealthTimeoutMs(700);

        EditorProcess.OpenResult r = ep.open(tmp.resolve("scene-a"));

        assertEquals("needs_manual", r.status());
        assertEquals("", r.url());
        assertTrue(r.hint().contains("未能在"), "hint 应说明启动超时：" + r.hint());
        assertTrue(r.elapsedMs() >= 700, "应真的等满健康检查预算，实际 " + r.elapsedMs());
        assertFalse(ep.isRunning());
        assertEquals(1, launcher.spawned.size());
        assertFalse(launcher.spawned.get(0).alive, "超时后必须杀掉子进程，否则会留孤儿占端口");
    }

    /** ③ 找不到 gimpish → 降级 + 安装指引，且**不尝试 spawn**。 */
    @Test
    void unavailableReturnsManualHintWithoutLaunching() throws Exception {
        FakeLauncher launcher = new FakeLauncher();
        EditorProcess ep = new EditorProcess(tmp.resolve("no-such-gimpish").toString(), 8765, true);
        ep.setLauncher(launcher);

        EditorProcess.OpenResult r = ep.open(tmp.resolve("scene-a"));

        assertEquals("needs_manual", r.status());
        assertTrue(r.hint().contains("npm install -g gimpish"), r.hint());
        assertTrue(launcher.commands.isEmpty(), "不可用时不该起进程");
        assertFalse(ep.available());

        EditorProcess.Status s = ep.status();
        assertFalse(s.available());
        assertFalse(s.running());
        assertEquals(8765, s.port());
        assertEquals("", s.url());
        assertEquals("", s.scene());
        assertTrue(s.hint().contains("npm install -g gimpish"), s.hint());
    }

    /** ④ enabled=false → 直接降级，连 spawn 都不试（docs/68 §7）。 */
    @Test
    void disabledReturnsNeedsManualWithoutLaunching() throws Exception {
        FakeLauncher launcher = new FakeLauncher();
        Path exe = Files.writeString(tmp.resolve("gimpish.js"), "// stub");
        EditorProcess ep = new EditorProcess(exe.toString(), 8765, false);
        ep.setLauncher(launcher);

        EditorProcess.OpenResult r = ep.open(tmp.resolve("scene-a"));

        assertEquals("needs_manual", r.status());
        assertTrue(r.hint().contains("enabled=false"), r.hint());
        assertTrue(launcher.commands.isEmpty());
        assertFalse(ep.isRunning());
    }

    /** ⑤ 同目录且健康 → 复用，不重启（重启会让用户正开着的页面白屏）。 */
    @Test
    void sameDirIsReused() throws Exception {
        int port = stub(true);
        FakeLauncher launcher = new FakeLauncher();
        EditorProcess ep = newEditor(port, true);
        ep.setLauncher(launcher);
        ep.setVersionDetector(() -> "0.1.0");
        ep.setProbeTimeoutMs(500);
        ep.setHealthTimeoutMs(3_000);

        Path dir = tmp.resolve("scene-a");
        assertEquals("ok", ep.open(dir).status());
        assertEquals("ok", ep.open(dir).status());

        assertEquals(1, launcher.commands.size(), "同目录不应重启");
        assertTrue(launcher.spawned.get(0).alive);
    }

    /** ⑥ 换目录 → 停旧的再起新的，且旧进程确实被杀。 */
    @Test
    void differentDirRestarts() throws Exception {
        int port = stub(true);
        FakeLauncher launcher = new FakeLauncher();
        EditorProcess ep = newEditor(port, true);
        ep.setLauncher(launcher);
        ep.setVersionDetector(() -> "0.1.0");
        ep.setProbeTimeoutMs(500);
        ep.setHealthTimeoutMs(3_000);

        Path a = tmp.resolve("scene-a");
        Path b = tmp.resolve("scene-b");
        assertEquals("ok", ep.open(a).status());
        EditorProcess.OpenResult r = ep.open(b);

        assertEquals("ok", r.status());
        assertEquals(b.toAbsolutePath().normalize().toString(), r.scene());
        assertEquals(2, launcher.commands.size());
        assertFalse(launcher.spawned.get(0).alive, "换目录必须停掉旧进程");
        assertTrue(launcher.spawned.get(1).alive);
    }

    /** ⑦ stop 幂等：跑着 → true，再停 → false。 */
    @Test
    void stopIsIdempotent() throws Exception {
        int port = stub(true);
        FakeLauncher launcher = new FakeLauncher();
        EditorProcess ep = newEditor(port, true);
        ep.setLauncher(launcher);
        ep.setVersionDetector(() -> "0.1.0");
        ep.setProbeTimeoutMs(500);
        ep.setHealthTimeoutMs(3_000);

        assertEquals("ok", ep.open(tmp.resolve("scene-a")).status());
        assertTrue(ep.stop());
        assertFalse(ep.isRunning());
        assertFalse(ep.stop());
    }

    /**
     * 版本探测必须走 `package.json`：实测 gimpish 0.1.0 **没有** `--version` 选项，
     * 只靠子进程探测会永远拿到空串（`status.version` 永远是 ""）。
     */
    @Test
    void versionComesFromPackageJsonNotFromCliFlag() throws Exception {
        Path pkg = tmp.resolve("node_modules").resolve("gimpish");
        Files.createDirectories(pkg.resolve("bin"));
        Files.writeString(pkg.resolve("bin").resolve("gimpish.js"), "// stub\n", StandardCharsets.UTF_8);
        Files.writeString(pkg.resolve("package.json"), "{\"name\":\"gimpish\",\"version\":\"9.9.9\"}",
                StandardCharsets.UTF_8);

        assertEquals("9.9.9", EditorProcess.versionFromPackageJson(pkg.resolve("bin").resolve("gimpish.js")));
        assertEquals("", EditorProcess.versionFromPackageJson(tmp.resolve("not-a-gimpish-dir").resolve("x.js")));
    }

    /**
     * 端口被占的**可观测行为**：serve 起得来但立刻退出（`isAlive()==false`）。
     * 实现里 `awaitHealthy` 必须据此**提前降级**，而不是空等满 15s 健康预算 ——
     * 否则用户点一下要僵住十几秒才知道"端口被占"。
     */
    @Test
    void processDyingImmediatelyDegradesFastInsteadOfWaitingFullTimeout() throws Exception {
        int port = stub(true);   // 有服务在响应，但我们的子进程已经死了
        EditorProcess ep = newEditor(port, true);
        ep.setLauncher((cmd, cwd) -> new DeadProcess());
        ep.setVersionDetector(() -> "0.1.0");
        ep.setProbeTimeoutMs(500);
        ep.setHealthTimeoutMs(5_000);   // 给足预算，验证的是"提前返回"

        long t0 = System.currentTimeMillis();
        EditorProcess.OpenResult r = ep.open(tmp.resolve("scene-dying"));
        long elapsed = System.currentTimeMillis() - t0;

        assertEquals("needs_manual", r.status());
        assertFalse(r.hint().isBlank(), "降级必须带可执行提示，不能只是失败");
        assertTrue(elapsed < 3_000, "进程已死就该立刻降级，实测等了 " + elapsed + "ms");
    }

    /**
     * 并发 `open` 必须被互斥锁串行化：两个请求同时到，**绝不能起两个 serve**。
     * 做法：让 launcher 慢 300ms，两个线程同时 open 同一目录 —— 谁先拿到锁谁起进程，
     * 另一个进来时发现"同目录且健康"直接复用。
     */
    @Test
    void concurrentOpenLaunchesOnlyOneProcess() throws Exception {
        int port = stub(true);
        EditorProcess ep = newEditor(port, true);
        SlowLauncher launcher = new SlowLauncher();
        ep.setLauncher(launcher);
        ep.setVersionDetector(() -> "0.1.0");
        ep.setProbeTimeoutMs(500);
        ep.setHealthTimeoutMs(5_000);

        Path dir = tmp.resolve("scene-concurrent");
        List<EditorProcess.OpenResult> results = new ArrayList<>();
        CountDownLatch go = new CountDownLatch(1);
        List<Thread> threads = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            Thread t = new Thread(() -> {
                try {
                    go.await(5, TimeUnit.SECONDS);
                    EditorProcess.OpenResult r = ep.open(dir);
                    synchronized (results) {
                        results.add(r);
                    }
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
            });
            threads.add(t);
            t.start();
        }
        go.countDown();
        for (Thread t : threads) {
            t.join(10_000);
        }

        assertEquals(4, results.size(), "4 个并发请求都要拿到结果");
        assertTrue(results.stream().allMatch(r -> "ok".equals(r.status())), "都该是 ok");
        assertEquals(1, launcher.launchCount.get(), "并发 open 只允许起一个 serve，实际起了 " + launcher.launchCount);
    }

    /**
     * 端口被占：**前置探测**就该拦下，绝不 spawn。
     *
     * <p>为什么必须前置：实测 `gimpish serve` 在端口被占时**不会退出**（照样活着），
     * 所以"进程死了就快速降级"救不了这种情况 —— 只会白等满 15s 健康预算。
     */
    @Test
    void occupiedPortDegradesImmediatelyWithoutSpawning() throws Exception {
        try (ServerSocket holder = new ServerSocket()) {
            holder.bind(new InetSocketAddress("127.0.0.1", 0));
            int busy = holder.getLocalPort();

            EditorProcess ep = newEditor(busy, true);
            ep.setPortChecker(EditorProcess::portFree);   // 本用例要的就是**真实**探测
            FakeLauncher launcher = new FakeLauncher();
            ep.setLauncher(launcher);
            ep.setVersionDetector(() -> "0.1.0");
            ep.setHealthTimeoutMs(15_000);   // 故意给满预算：要证明的是"根本没走到健康检查"

            long t0 = System.currentTimeMillis();
            EditorProcess.OpenResult r = ep.open(tmp.resolve("scene-busy"));
            long took = System.currentTimeMillis() - t0;

            assertEquals("needs_manual", r.status());
            assertTrue(r.hint().contains(String.valueOf(busy)), "提示要点名是哪个端口：" + r.hint());
            assertTrue(launcher.commands.isEmpty(), "端口被占时**不该** spawn 任何进程");
            assertTrue(took < 6_000, "前置探测应当很快，实测 " + took + "ms");
        }
    }

    // ---------------- 替身与 stub ----------------

    private EditorProcess newEditor(int port, boolean enabled) throws IOException {
        Path exe = Files.writeString(tmp.resolve("gimpish.js"), "// test stub entry");
        EditorProcess ep = new EditorProcess(exe.toString(), port, enabled);
        // 这些用例的 HTTP stub 就监听在编辑器端口上（用来模拟"serve 已就绪"），
        // 真实的 bind 探测必然报"被占" → 默认放行。端口探测本身另有用例覆盖。
        ep.setPortChecker(p -> true);
        return ep;
    }

    /** 起得来又立刻退出（端口被占时上游的真实表现）。 */
    private static final class DeadProcess implements EditorProcess.ManagedProcess {
        @Override
        public boolean isAlive() {
            return false;
        }

        @Override
        public InputStream stdout() {
            return InputStream.nullInputStream();
        }

        @Override
        public InputStream stderr() {
            return InputStream.nullInputStream();
        }

        @Override
        public void destroy() {
        }

        @Override
        public void destroyForcibly() {
        }

        @Override
        public void destroyDescendants() {
        }
    }

    /** 启动耗时 300ms 的替身：把"两个请求同时进来"的时间窗撑开。 */
    private static final class SlowLauncher implements EditorProcess.Launcher {

        final java.util.concurrent.atomic.AtomicInteger launchCount = new java.util.concurrent.atomic.AtomicInteger();

        @Override
        public EditorProcess.ManagedProcess launch(List<String> cmd, Path cwd) throws IOException {
            launchCount.incrementAndGet();
            try {
                Thread.sleep(300);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return new FakeProcess();
        }
    }

    /**
     * 起一个本地 {@code /api/scene} stub。
     *
     * @param respond {@code true} 立即 200；{@code false} 挂住不返回（模拟"服务起来了但场景没加载"）
     */
    private int stub(boolean respond) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/scene", exchange -> {
            try {
                if (!respond) {
                    release.await(5, TimeUnit.SECONDS);
                }
                byte[] body = "{\"version\":1}".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
            } catch (Exception ignored) {
                // 测试收尾时连接已关：无需处理
            } finally {
                exchange.close();
            }
        });
        server.start();
        return server.getAddress().getPort();
    }

    /** 记录命令、返回"永远活着"的假进程。 */
    private static final class FakeLauncher implements EditorProcess.Launcher {

        final List<List<String>> commands = new ArrayList<>();
        final List<FakeProcess> spawned = new ArrayList<>();

        @Override
        public EditorProcess.ManagedProcess launch(List<String> cmd, Path cwd) {
            commands.add(cmd);
            FakeProcess p = new FakeProcess();
            spawned.add(p);
            return p;
        }
    }

    private static final class FakeProcess implements EditorProcess.ManagedProcess {

        volatile boolean alive = true;

        @Override
        public boolean isAlive() {
            return alive;
        }

        @Override
        public InputStream stdout() {
            return InputStream.nullInputStream();
        }

        @Override
        public InputStream stderr() {
            return InputStream.nullInputStream();
        }

        @Override
        public void destroy() {
            alive = false;
        }

        @Override
        public void destroyForcibly() {
            alive = false;
        }

        @Override
        public void destroyDescendants() {
            // 替身没有后代
        }
    }
}
