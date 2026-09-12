package com.polyface.backend.observability;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

/**
 * FR-71 trace 贯穿的 Java 侧验证。
 *
 * <p>只覆盖**不依赖 Python** 的部分（Filter 生命周期与响应头），
 * 跨服务传播由端到端脚本 {@code scripts/e2e_fr70.py} 覆盖。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "polyface.data-dir=target/test-data-trace")
class TraceFilterTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void responseCarriesTraceIdHeader() throws Exception {
        mockMvc.perform(get("/health"))
                .andExpect(status().isOk())
                .andExpect(header().exists(TraceContext.HEADER));
    }

    @Test
    void traceIdIs32HexW3cCompatible() throws Exception {
        MvcResult res = mockMvc.perform(get("/health")).andReturn();
        String tid = res.getResponse().getHeader(TraceContext.HEADER);
        assertNotNull(tid, "响应必须带 trace id");
        assertEquals(32, tid.length(), "W3C trace id 应为 32 位 hex");
        assertTrue(tid.matches("[0-9a-f]{32}"), "必须是 32 位小写 hex，实际：" + tid);
    }

    @Test
    void traceContextClearedAfterRequest() throws Exception {
        mockMvc.perform(get("/health")).andReturn();
        assertNull(TraceContext.get(),
                "请求结束后必须 clear，否则线程复用会导致 trace 串号");
    }

    @Test
    void distinctRequestsGetDistinctTraceIds() throws Exception {
        String a = mockMvc.perform(get("/health")).andReturn().getResponse().getHeader(TraceContext.HEADER);
        String b = mockMvc.perform(get("/health")).andReturn().getResponse().getHeader(TraceContext.HEADER);
        assertNotNull(a);
        assertNotNull(b);
        assertTrue(!a.equals(b), "不同请求应有不同 trace id");
    }

    @Test
    void getOrCreateAutoGeneratesWhenAbsent() {
        TraceContext.clear();
        String tid = TraceContext.getOrCreate();
        assertNotNull(tid);
        assertEquals(32, tid.length());
        // 已存在时应复用而非新建
        assertEquals(tid, TraceContext.getOrCreate());
        TraceContext.clear();
    }
}
