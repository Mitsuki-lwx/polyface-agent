package com.polyface.backend.web;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/**
 * FR-51 入料接口的**本地校验**测试。
 * 只覆盖不经过 Python 的分支（缺参 / 路径 / 扩展名 / media_id 安全），
 * 避免单元测试依赖 Python 服务；跨层链路由端到端脚本覆盖。
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "polyface.data-dir=target/test-data-ingest")
class IngestControllerTest {

    private static final String PROBE = "/api/ingest/probe";
    private static final String UPLOAD = "/api/ingest/upload";

    @Autowired
    private MockMvc mockMvc;

    private static String slash(Path p) {
        return p.toString().replace("\\", "/");
    }

    @Test
    void probeWithoutParamsReturns400() throws Exception {
        mockMvc.perform(post(PROBE).contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void probeMissingFileReturns400() throws Exception {
        mockMvc.perform(post(PROBE).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"D:/definitely/not/here.mp4\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void probeDirectoryReturns400(@TempDir Path dir) throws Exception {
        mockMvc.perform(post(PROBE).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"" + slash(dir) + "\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void probeUnsupportedExtensionReturns400(@TempDir Path dir) throws Exception {
        Path f = dir.resolve("note.txt");
        Files.writeString(f, "hello");
        mockMvc.perform(post(PROBE).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"path\":\"" + slash(f) + "\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void probeIllegalMediaIdReturns400() throws Exception {
        mockMvc.perform(post(PROBE).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"media_id\":\"../../etc/passwd\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void uploadRejectsDisallowedExtension() throws Exception {
        MockMultipartFile f = new MockMultipartFile(
                "file", "payload.exe", "application/octet-stream", "x".getBytes());
        mockMvc.perform(multipart(UPLOAD).file(f))
                .andExpect(status().isBadRequest());
    }

    @Test
    void uploadRejectsEmptyFile() throws Exception {
        MockMultipartFile f = new MockMultipartFile(
                "file", "empty.mp4", "video/mp4", new byte[0]);
        mockMvc.perform(multipart(UPLOAD).file(f))
                .andExpect(status().isBadRequest());
    }

    @Test
    void uploadAcceptsMediaExtensionAndReturnsMediaId() throws Exception {
        MockMultipartFile f = new MockMultipartFile(
                "file", "口播.mp4", "video/mp4", "fake-bytes".getBytes());
        mockMvc.perform(multipart(UPLOAD).file(f))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.media_id").exists())
                .andExpect(jsonPath("$.filename").value("口播.mp4"));
    }

    @Test
    void deleteUnknownMediaReturns404() throws Exception {
        mockMvc.perform(delete("/api/ingest/media/deadbeef0000.mp4"))
                .andExpect(status().isNotFound());
    }
}
