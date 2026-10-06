package com.polyface.backend.media;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

/**
 * 媒体目录的**唯一来源**：入料副本（FR-51）与封面产物（M6-1）都落在这里。
 *
 * <p>之所以抽成 Bean 而不是各自解析配置：两个控制器若分别按
 * {@code polyface.media.dir}/{@code polyface.data-dir} 推导，配置一旦不一致就会出现
 * "封面写得进、/api/media 读不出"的割裂。集中解析后调用方只拿 {@link #root()}。
 */
@Component
public class MediaDir {

    private static final Logger log = LoggerFactory.getLogger(MediaDir.class);

    private final Path root;

    public MediaDir(@Value("${polyface.media.dir:}") String dirOverride,
                    @Value("${polyface.data-dir:../data}") String dataDir) throws IOException {
        String dir = (dirOverride == null || dirOverride.isBlank())
                ? Path.of(dataDir).toAbsolutePath().resolve("media").toString()
                : dirOverride;
        this.root = Path.of(dir).toAbsolutePath().normalize();
        Files.createDirectories(this.root);
        log.info("media dir: {}", this.root);
    }

    /** 媒体根目录（绝对路径、已 normalize）。 */
    public Path root() {
        return root;
    }

    /**
     * 把相对路径解析到根目录内，并阻断目录穿越。
     * 越界（{@code ../} 逃逸、Windows 盘符绝对路径等）一律 400 —— 这是对外托管文件的
     * 安全边界，宁可拒绝也不把根目录外的任意文件当媒体读出。
     */
    public Path resolveSafe(String relative) {
        if (relative == null || relative.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少媒体路径");
        }
        // 统一分隔符后再交给 Path 解析，避免 Windows 下 '\' 绕过 normalize
        String cleaned = relative.replace('\\', '/');
        while (cleaned.startsWith("/")) {
            cleaned = cleaned.substring(1);
        }
        Path p = root.resolve(cleaned).normalize();
        if (!p.startsWith(root)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "非法的媒体路径");
        }
        return p;
    }
}
