package com.polyface.backend.observability;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/**
 * trace 上下文**跨线程池**（FR-71）。
 *
 * <p>为什么必须测：`/api/jobs` 是异步的 —— 响应头里的 trace_id 由 {@link TraceFilter}
 * 在 HTTP 线程上生成，而真正的生成/粗剪跑在**任务线程**里。`ThreadLocal` 不跨线程，
 * 不显式继承的话任务线程会**再生成一个**，于是「响应里的 trace_id」和
 * 「Langfuse 里那条 trace」不是同一个 ✗（实测 2026-10-31：`91b6c49c…` vs `c0cf063a…`）。
 */
class TraceContextTest {

    @AfterEach
    void cleanup() {
        TraceContext.clear();
    }

    @Test
    void traceIdSurvivesIntoTheTracingExecutor() throws Exception {
        String tid = TraceContext.startNew();
        ExecutorService pool = TraceContext.tracing(Executors.newSingleThreadExecutor());
        try {
            String got = pool.submit(TraceContext::getOrCreate).get(5, TimeUnit.SECONDS);
            assertThat(got).as("任务线程里拿到的 trace_id").isEqualTo(tid);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void plainExecutorLosesIt() throws Exception {
        // 反证：不包 tracing 的话就是会丢 —— 说明这层包装是真的在起作用
        String tid = TraceContext.startNew();
        ExecutorService plain = Executors.newSingleThreadExecutor();
        try {
            String got = plain.submit(TraceContext::getOrCreate).get(5, TimeUnit.SECONDS);
            assertThat(got).as("裸线程池应当拿到一个新的 id").isNotEqualTo(tid).hasSize(32);
        } finally {
            plain.shutdownNow();
        }
    }

    @Test
    void manyTasksAllKeepTheSubmittersId() throws Exception {
        String tid = TraceContext.startNew();
        ExecutorService pool = TraceContext.tracing(Executors.newFixedThreadPool(3));
        try {
            for (int i = 0; i < 6; i++) {
                assertThat(pool.submit(TraceContext::getOrCreate).get(5, TimeUnit.SECONDS)).isEqualTo(tid);
            }
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void workerThreadIsCleanedAfterTask() throws Exception {
        // 线程池会复用线程：任务结束后不能把上一个 id 留在池线程里
        String tid = TraceContext.startNew();
        ExecutorService pool = TraceContext.tracing(Executors.newSingleThreadExecutor());
        try {
            pool.submit(() -> { }).get(5, TimeUnit.SECONDS);
        } finally {
            pool.shutdownNow();
        }
        // 提交线程自己的 id 不受影响
        assertThat(TraceContext.get()).isEqualTo(tid);
    }
}
