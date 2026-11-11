package com.polyface.backend.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

/**
 * 单平台重试的目标筛选。
 *
 * <p>为什么单测：传错一个平台名就会**重跑已经成功的平台** —— 白花一次额度，
 * 而且结果上看起来一切正常（稿子被覆盖成新版）。这条闸门只能靠测试钉住。
 */
class RetryTargetsTest {

    @Test
    void withoutBodyRetriesAllFailed() {
        assertThat(JobController.retryTargets(List.of("zhihu", "gzh"), null))
                .containsExactly("zhihu", "gzh");
        assertThat(JobController.retryTargets(List.of("zhihu", "gzh"), List.of()))
                .containsExactly("zhihu", "gzh");
    }

    @Test
    void picksOnlyRequestedFailedPlatforms() {
        assertThat(JobController.retryTargets(List.of("zhihu", "gzh"), List.of("gzh")))
                .containsExactly("gzh");
    }

    @Test
    void deduplicatesRepeatedRequest() {
        assertThat(JobController.retryTargets(List.of("zhihu", "gzh"), List.of("gzh", "gzh")))
                .containsExactly("gzh");
    }

    @Test
    void rejectsPlatformThatDidNotFail() {
        assertThatThrownBy(() ->
                JobController.retryTargets(List.of("zhihu"), List.of("xhs")))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("不在失败列表");
    }

    @Test
    void dropsMixedUnknownAndKeepsValid() {
        // 混合请求：合法的那一个照跑（不因为多余项整单失败）
        assertThat(JobController.retryTargets(List.of("zhihu"), List.of("xhs", "zhihu")))
                .containsExactly("zhihu");
    }
}
