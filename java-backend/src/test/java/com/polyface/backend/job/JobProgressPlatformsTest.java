package com.polyface.backend.job;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * 平台级状态解析：生成任务要能**边跑边显示已完成的平台**。
 *
 * <p>为什么单测它：状态错一次就是"明明跑完了还显示 ⏳"，用户会以为卡死；
 * 而事件文件可能写半行（两个进程追加同一个文件），所以容错也要测。
 */
class JobProgressPlatformsTest {

    @TempDir
    Path dir;

    private Path events(String... lines) throws Exception {
        Path f = dir.resolve("events.jsonl");
        Files.write(f, String.join("\n", lines).getBytes(StandardCharsets.UTF_8));
        return f;
    }

    /** 与 Java/Python 两侧真实写入的字段形状一致（fields.platform）。 */
    private String ev(String stage, String kind, String platform) {
        return ev(stage, kind, platform, "");
    }

    private String ev(String stage, String kind, String platform, String error) {
        return "{\"run_id\":\"java\",\"stage\":\"" + stage + "\",\"kind\":\"" + kind
                + "\",\"fields\":{\"platform\":\"" + platform + "\""
                + (error.isEmpty() ? "" : ",\"error\":\"" + error + "\"") + "},\"message\":\"\"}";
    }

    @Test
    void readsStartEndFailPerPlatform() throws Exception {
        Path f = events(
                ev("理解", "start", ""),
                ev("平台", "start", "xhs"),
                ev("平台", "end", "xhs"),
                ev("平台", "start", "zhihu"),
                ev("平台", "fail", "zhihu"));

        Map<String, JobProgress.PlatformState> st = JobProgress.readPlatforms(f);
        assertThat(st.get("xhs").state()).isEqualTo(JobProgress.DONE);
        assertThat(st.get("zhihu").state()).isEqualTo(JobProgress.FAILED);
        assertThat(st.get("zhihu").error()).isEmpty();
    }

    @Test
    void lastEventWinsForSamePlatform() throws Exception {
        Path f = events(ev("平台", "start", "xhs"), ev("平台", "end", "xhs"));
        assertThat(JobProgress.readPlatforms(f).get("xhs").state()).isEqualTo(JobProgress.DONE);
    }

    @Test
    void skipsHalfWrittenLinesAndUnknownStages() throws Exception {
        Path f = events(
                ev("平台", "start", "xhs"),
                "{\"stage\":\"平台\",\"kind\":\"start\"");      // 半行 JSON
        Map<String, JobProgress.PlatformState> st = JobProgress.readPlatforms(f);
        assertThat(st).hasSize(1);
        assertThat(st.get("xhs").state()).isEqualTo(JobProgress.RUNNING);
    }

    @Test
    void failureReasonIsCarriedWithStatus() throws Exception {
        Path f = events(ev("平台", "start", "zhihu"),
                        ev("平台", "fail", "zhihu", "模型超时"));
        assertThat(JobProgress.readPlatforms(f).get("zhihu").error()).isEqualTo("模型超时");
    }

    @Test
    void missingFileIsEmptyNotThrow() {
        assertThat(JobProgress.readPlatforms(dir.resolve("nope.jsonl"))).isEmpty();
        assertThat(JobProgress.readPlatforms(null)).isEmpty();
    }
}
