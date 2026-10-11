package com.polyface.backend.job;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 启动清理：未结束的任务要标成**可继续**，不是失败。
 *
 * <p>为什么单测这条：它是"服务重启后任务不会白跑"的唯一开关。改回 {@code failed}
 * 之后，已跑过的部分还在磁盘上，但用户再也看不到「继续」按钮 —— 而且**没有任何测试会红**
 * （这条测试就是为了堵住那个口子；端到端也验过，但端到端要几分钟且要真杀进程）。
 */
class GenerateRunnerRecoverTest {

    @TempDir
    Path tempDir;

    private GenerateRunner runner(Store store) {
        return new GenerateRunner(store, mock(PythonClient.class), mock(GenerateService.class),
                tempDir.toString(), 300, 50);
    }

    @Test
    void inFlightJobsBecomeInterruptedNotFailed() {
        Store store = new Store(tempDir.toString());
        long running = store.insertJob("generate", "{}", null, "1",
                tempDir.resolve("w1").toString(), tempDir.resolve("w1/e.jsonl").toString());
        store.markJobRunning(running);
        long queued = store.insertJob("roughcut", "{}", null, "2",
                tempDir.resolve("w2").toString(), tempDir.resolve("w2/e.jsonl").toString());

        runner(store);                       // 构造即触发 recoverInterrupted

        assertThat(store.getJob(running).orElseThrow().status())
                .isEqualTo(Store.JOB_INTERRUPTED);
        assertThat(store.getJob(queued).orElseThrow().status())
                .isEqualTo(Store.JOB_INTERRUPTED);
    }

    @Test
    void finishedJobsAreLeftAlone() {
        Store store = new Store(tempDir.toString());
        long ok = store.insertJob("generate", "{}", null, "1",
                tempDir.resolve("w").toString(), tempDir.resolve("w/e.jsonl").toString());
        store.finishJobWithResult(ok, Store.JOB_SUCCEEDED, null, "{}");

        runner(store);

        // 已结束的任务不该被启动清理碰 —— 否则历史会被改写
        assertThat(store.getJob(ok).orElseThrow().status()).isEqualTo(Store.JOB_SUCCEEDED);
    }

    @Test
    void interruptedJobKeepsItsWorkdirSoResumeCanFindTheDoneFiles() {
        Store store = new Store(tempDir.toString());
        Path wd = tempDir.resolve("job-12345");
        long id = store.insertJob("generate", "{}", null, "1",
                wd.toString(), wd.resolve("e.jsonl").toString());
        store.markJobRunning(id);

        runner(store);

        // 续跑靠的是"原目录里的 done/" —— 目录丢了，续跑就退化成重跑
        assertThat(store.getJob(id).orElseThrow().workdir()).isEqualTo(wd.toString());
    }
}
