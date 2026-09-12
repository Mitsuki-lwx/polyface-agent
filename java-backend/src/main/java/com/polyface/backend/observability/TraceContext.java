package com.polyface.backend.observability;

import java.util.UUID;

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
}
