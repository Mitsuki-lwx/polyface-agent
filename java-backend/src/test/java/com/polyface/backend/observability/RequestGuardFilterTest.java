package com.polyface.backend.observability;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 请求来源准入（`openspec/changes/local-service-request-guard`，spec: local-service-guard）。
 *
 * <p>两个方向都要钉：**该拒的必拒**（DNS rebinding / 跨站），
 * 以及**不该拒的一条也不许拒**（工作台同源、命令行、Java→Python 服务间调用）。
 * 后者尤其要紧 —— 这道闸门判错会把正常链路一起打死。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-guard",
        "polyface.media.dir=target/test-data-guard/media",
        // 其它测试默认关掉闸门（见 src/test/resources/application.properties），这里显式打开
        "polyface.guard.enabled=true",
})
class RequestGuardFilterTest {

    @Autowired
    private MockMvc mockMvc;

    @Test
    void forgedHostIsRejected() throws Exception {
        // ① DNS rebinding 的落地形态：浏览器认为同源，但 Host 头是攻击者域名
        mockMvc.perform(get("/api/materials").header("Host", "evil.example.com"))
                .andExpect(status().isForbidden());
    }

    @Test
    void forgedHostDoesNotLeakData() throws Exception {
        String body = mockMvc.perform(get("/api/materials").header("Host", "evil.example.com"))
                .andReturn().getResponse().getContentAsString();
        org.assertj.core.api.Assertions.assertThat(body)
                .as("403 正文不得含业务数据，且要指出配置项名")
                .doesNotContain("draft_count")
                .contains("POLYFACE_ALLOWED_ORIGINS");
    }

    @Test
    void crossSiteOriginIsRejected() throws Exception {
        // ② 跨站简单请求
        mockMvc.perform(get("/api/materials").header("Origin", "https://evil.example.com"))
                .andExpect(status().isForbidden());
    }

    @Test
    void loopbackHostWithoutOriginPasses() throws Exception {
        // 命令行 / Java→Python：不带 Origin（MockMvc 默认不带 Host，这里显式给回环的）
        mockMvc.perform(get("/api/materials").header("Host", "127.0.0.1:8080"))
                .andExpect(status().isOk());
    }

    @Test
    void loopbackOriginPasses() throws Exception {
        // 工作台是同源页面
        mockMvc.perform(get("/api/materials")
                        .header("Host", "127.0.0.1:8080")
                        .header("Origin", "http://127.0.0.1:8080"))
                .andExpect(status().isOk());
    }

    @Test
    void ipv6LoopbackPasses() throws Exception {
        mockMvc.perform(get("/api/materials")
                        .header("Host", "[::1]:8080")
                        .header("Origin", "http://[::1]:8080"))
                .andExpect(status().isOk());
    }

    @Test
    void staticSpaIsAlsoGuarded() throws Exception {
        // 闸门在最外层：连静态资源与根路径也走它（伪造 Host 拿不到页面）
        mockMvc.perform(get("/").header("Host", "evil.example.com"))
                .andExpect(status().isForbidden());
    }

    @Test
    void happyPathStillWorks() throws Exception {
        mockMvc.perform(get("/api/materials").header("Host", "localhost"))
                .andExpect(status().isOk());
    }
}
