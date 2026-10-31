package com.polyface.backend.observability;

import java.util.UUID;
import java.util.concurrent.ExecutorService;

/**
 * 链路标识上下文（FR-71）。
 *
 * <p>一次用户操作的 trace_id 由此产生，并通过 {@code X-Trace-Id} 请求头传给 Python 服务，
 * 使「Java 编排 → Python 管线 → LLM 调用」全程可关联。
 *
 * <p>⚠️ 使用 {@link ThreadLocal} 存储 —— Spring MVC 一个请求一个线程，需在入口
 * {@link #startNew()}、出口 {@link #clear()}（务必放在 finally，避免线程池复用串号）。
 */
public final class TraceContext {

    public static final String HEADER = "X-Trace-Id";

    private static final ThreadLocal<String> CURRENT = new ThreadLocal<>();

    private TraceContext() {
    }

    /** 生成新的 trace_id 并绑定到当前线程。 */
    public static String startNew() {
        // 32 位 hex：遵循 W3C Trace Context（Langfuse v4 的 trace_id 规范）
        String tid = UUID.randomUUID().toString().replace("-", "");
        CURRENT.set(tid);
        return tid;
    }

    /** 读取当前 trace_id；不存在则生成（保证非空）。 */
    public static String getOrCreate() {
        String tid = CURRENT.get();
        if (tid == null || tid.isBlank()) {
            tid = startNew();
        }
        return tid;
    }

    /** 读取（可能为 null）。 */
    public static String get() {
        return CURRENT.get();
    }

    /** 清理（必须放在 finally）。 */
    public static void clear() {
        CURRENT.remove();
    }

    /**
     * 把当前线程的 trace_id 带进任务；任务线程用完还原（线程池会复用线程）。
     *
     * <p><b>为什么必须做</b>：`/api/jobs` 是**异步**的 —— 响应头里的 trace_id 由
     * {@link TraceFilter} 在 **HTTP 线程**上生成，而真正的生成/粗剪跑在**任务线程**里。
     * `ThreadLocal` 不跨线程，于是任务线程里 {@link #getOrCreate()} 会**再生成一个**，
     * 导致「响应里的 trace_id」和「Langfuse 里那条 trace」**不是同一个** ✗
     * （实测 2026-10-31：响应头 `91b6c49c…`，Langfuse 上 `c0cf063a…`）。
     */
    public static Runnable inherit(Runnable task) {
        String tid = getOrCreate();
        return () -> {
            String prev = CURRENT.get();
            CURRENT.set(tid);
            try {
                task.run();
            } finally {
                if (prev == null) {
                    CURRENT.remove();
                } else {
                    CURRENT.set(prev);
                }
            }
        };
    }

    /**
     * 包一层 {@link ExecutorService}：**提交时的上下文**跟着任务走。
     *
     * <p>用它替换四个 Runner 里的 {@code Executors.newSingleThreadExecutor(...)} 即可 ——
     * 不用去改每一处 {@code pool.submit(...)}，将来新加的提交点也自动被覆盖。
     */
    public static ExecutorService tracing(ExecutorService inner) {
        return new java.util.concurrent.AbstractExecutorService() {
            @Override
            public void execute(Runnable command) {
                inner.execute(inherit(command));
            }

            @Override
            public void shutdown() {
                inner.shutdown();
            }

            @Override
            public java.util.List<Runnable> shutdownNow() {
                return inner.shutdownNow();
            }

            @Override
            public boolean isShutdown() {
                return inner.isShutdown();
            }

            @Override
            public boolean isTerminated() {
                return inner.isTerminated();
            }

            @Override
            public boolean awaitTermination(long timeout, java.util.concurrent.TimeUnit unit)
                    throws InterruptedException {
                return inner.awaitTermination(timeout, unit);
            }
        };
    }
}
