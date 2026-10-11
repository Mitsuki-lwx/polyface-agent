package com.polyface.backend.web;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.polyface.backend.client.PythonClient;
import com.polyface.backend.store.Store;

/**
 * 「继续」端点的状态闸门（`docs/spec_resume.md` §5、`docs/checklist_resume.md` §6）。
 *
 * <p>为什么这几条要紧：resume 与 retry 的**行为不同**（跳过 vs 从头）。状态闸门一旦松掉，
 * 用户会以为自己"接着跑了"，其实把已经跑过的部分又花了一遍额度 —— 而且看不出来。
 * 所以状态不对时必须**明确报错**，不能静默接受。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-resume",
        "polyface.llm.timeout-sec=240",
        "polyface.llm.fast-timeout-sec=60",
})
class JobResumeTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private Store store;

    @org.springframework.boot.test.mock.mockito.MockBean
    private PythonClient python;

    /** 造一个任务并直接把它置成给定状态（不真的跑）。 */
    private long job(String kind, String status) {
        long id = store.insertJob(kind, "{\"material_id\":999999,\"platforms\":[\"xhs\"]}",
                null, "999999", "target/test-data-resume/w" + System.nanoTime(),
                "target/test-data-resume/e.jsonl");
        switch (status) {
            case Store.JOB_RUNNING -> store.markJobRunning(id);
            case Store.JOB_INTERRUPTED -> store.markJobInterrupted(id);
            case Store.JOB_FAILED -> store.finishJob(id, Store.JOB_FAILED, "跑错了", null, null, null);
            case Store.JOB_SUCCEEDED -> store.finishJob(id, Store.JOB_SUCCEEDED, null, "o", "s", "c");
            default -> { }
        }
        return id;
    }

    @Test
    void resumeAcceptsInterruptedJob() throws Exception {
        long id = job("generate", Store.JOB_INTERRUPTED);

        mockMvc.perform(post("/api/jobs/" + id + "/resume"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.resumed").value(true))
                .andExpect(jsonPath("$.job_id").value(id));
    }

    @Test
    void resumeRejectsFailedJobWithPointerToRetry() throws Exception {
        long id = job("generate", Store.JOB_FAILED);

        mockMvc.perform(post("/api/jobs/" + id + "/resume"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("retry")));
    }

    @Test
    void resumeRejectsJobThatIsNotInterrupted() throws Exception {
        for (String st : new String[] {Store.JOB_SUCCEEDED, Store.JOB_RUNNING, "queued"}) {
            long id = job("generate", st);
            mockMvc.perform(post("/api/jobs/" + id + "/resume"))
                    .andExpect(status().isConflict())
                    .andExpect(jsonPath("$.detail").value(
                            org.hamcrest.Matchers.containsString("已中断")));
        }
    }

    @Test
    void resumeRejectsUnknownJob() throws Exception {
        mockMvc.perform(post("/api/jobs/987654/resume"))
                .andExpect(status().isNotFound());
    }

    @Test
    void retryOnInterruptedJobPointsToResume() throws Exception {
        long id = job("generate", Store.JOB_INTERRUPTED);

        // retry 会**从头**重跑 —— 对中断的任务该拦下来，并指向 resume
        mockMvc.perform(post("/api/jobs/" + id + "/retry"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.detail").value(
                        org.hamcrest.Matchers.containsString("resume")));
    }

    @Test
    void interruptedJobCanBeAbandoned() throws Exception {
        long id = job("generate", Store.JOB_INTERRUPTED);

        // 已中断的任务没有进程可杀，但必须能被放弃 —— 否则永远躺在"可继续"里
        mockMvc.perform(post("/api/jobs/" + id + "/cancel"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.canceled").value(true))
                .andExpect(jsonPath("$.status").value(Store.JOB_CANCELED));

        // 放弃之后就不再是"可继续"了
        assertThat(store.getJob(id).orElseThrow().status()).isEqualTo(Store.JOB_CANCELED);
        mockMvc.perform(post("/api/jobs/" + id + "/resume"))
                .andExpect(status().isConflict());
    }

    @Test
    void roughcutJobAlsoSupportsResume() throws Exception {
        long id = job("roughcut", Store.JOB_INTERRUPTED);

        mockMvc.perform(post("/api/jobs/" + id + "/resume"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.resumed").value(true));
    }
}
