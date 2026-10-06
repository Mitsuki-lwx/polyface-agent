package com.polyface.backend.web;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.polyface.backend.media.MediaDir;

/**
 * 媒体托管（FR-51 副本 / M6-1 封面）：把 {@link MediaDir} 下的文件按原字节回给浏览器。
 *
 * <p>只读、只在本机：前端展示封面 PNG 需要能直接 {@code <img src>}，而不是再走一次 base64。
 * 目录穿越由 {@link MediaDir#resolveSafe} 统一拦截，此处不再自行拼路径。
 */
@RestController
public class MediaController {

    private final MediaDir mediaDir;

    public MediaController(MediaDir mediaDir) {
        this.mediaDir = mediaDir;
    }

    /** 捕获式路径 {@code {*rel}}：保留子目录结构（封面是 covers/{stem}/cover.png）。 */
    @GetMapping("/api/media/{*rel}")
    public ResponseEntity<Resource> serve(@PathVariable("rel") String rel) throws IOException {
        Path target = mediaDir.resolveSafe(rel);
        if (!Files.isRegularFile(target)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "媒体文件不存在");
        }
        return ResponseEntity.ok()
                .contentType(contentTypeOf(target.getFileName().toString()))
                .contentLength(Files.size(target))
                .body(new FileSystemResource(target));
    }

    /** 按扩展名给出 Content-Type；未知类型退化为二进制流，交给浏览器按下载处理。 */
    private static MediaType contentTypeOf(String filename) {
        int dot = filename.lastIndexOf('.');
        String ext = dot < 0 ? "" : filename.substring(dot + 1).toLowerCase();
        return switch (ext) {
            case "png" -> MediaType.IMAGE_PNG;
            case "jpg", "jpeg" -> MediaType.IMAGE_JPEG;
            case "gif" -> MediaType.IMAGE_GIF;
            case "webp" -> MediaType.parseMediaType("image/webp");
            case "svg" -> MediaType.parseMediaType("image/svg+xml");
            case "mp4", "m4v" -> MediaType.parseMediaType("video/mp4");
            case "webm" -> MediaType.parseMediaType("video/webm");
            case "mov" -> MediaType.parseMediaType("video/quicktime");
            case "mkv" -> MediaType.parseMediaType("video/x-matroska");
            case "mp3" -> MediaType.parseMediaType("audio/mpeg");
            case "wav" -> MediaType.parseMediaType("audio/wav");
            case "m4a" -> MediaType.parseMediaType("audio/mp4");
            case "aac" -> MediaType.parseMediaType("audio/aac");
            case "flac" -> MediaType.parseMediaType("audio/flac");
            case "ogg" -> MediaType.parseMediaType("audio/ogg");
            case "json" -> MediaType.APPLICATION_JSON;
            case "txt", "md" -> MediaType.TEXT_PLAIN;
            default -> MediaType.APPLICATION_OCTET_STREAM;
        };
    }
}
