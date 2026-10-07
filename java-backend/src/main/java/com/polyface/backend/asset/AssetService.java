package com.polyface.backend.asset;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;
import com.polyface.backend.web.MediaController;

/**
 * 统一资产库（M7-1）：文件布局 + 登记 + 删除语义。
 *
 * <p>落盘规则见 docs/73 §3：**新上传**走内容寻址 {@code assets/<sha前2>/<sha>.<ext>}，
 * 既有的入料副本（{@code {media}/<uuid>.<ext>}）与封面产物（{@code {media}/covers/...}）
 * **不搬家**，只登记 rel_path —— 移动用户既有文件是破坏性操作（ADR-021 决策 2）。
 *
 * <p>删除语义见 docs/73 §6：解链 → 删记录 → **仅当再无任何记录引用同 sha 且 sha 非空**时删文件；
 * 绝不静默删用户文件（ADR-017 的数据姿态）。
 */
@Component
public class AssetService {

    private static final Logger log = LoggerFactory.getLogger(AssetService.class);

    /** 上传缓冲区：8KB，够小不占内存，够大不至于每几字节一次系统调用。 */
    private static final int BUFFER_BYTES = 8192;

    /** 扩展名 → kind 白名单（docs/73 §4.3）。上传与登记共用同一张表，避免两处规则漂移。 */
    private static final Map<String, String> KIND_BY_EXT = buildKindByExt();

    private static Map<String, String> buildKindByExt() {
        Map<String, String> m = new LinkedHashMap<>();
        for (String e : List.of("png", "jpg", "jpeg", "webp", "gif", "bmp", "svg")) {
            m.put(e, "image");
        }
        for (String e : List.of("mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "wma")) {
            m.put(e, "audio");
        }
        for (String e : List.of("mp4", "mov", "mkv", "avi", "webm", "flv", "m4v", "wmv", "mpg", "mpeg", "ts")) {
            m.put(e, "video");
        }
        // `timeline` 是"时间轴类产物"：粗剪的剪点（json）与字幕（srt/vtt/ass）都归这里。
        // ⚠️ 漏掉 srt 的后果实测过：粗剪产物登记时它抛错，把后面的**剪点登记与源视频关联**一起带崩，
        // 于是"任务成功但素材库里只有成片、还看不到它从哪个视频来"。
        for (String e : List.of("json", "srt", "vtt", "ass", "ssa")) {
            m.put(e, "timeline");
        }
        return Map.copyOf(m);
    }

    /** 资产的对外形状（docs/73 §4.2，字段名逐字对齐；snake_case 沿用仓库既有 DTO 约定）。 */
    public record AssetDto(long id, String kind, String source, String name, String rel_path, String url,
                           String mime, long size_bytes, int width, int height, double duration_sec,
                           String sha256, List<String> tags, String created_at, List<LinkDto> links) {
    }

    public record LinkDto(String owner_kind, long owner_id) {
    }

    /** 删除结果（docs/73 §6）：是否真的删了物理文件 + 解掉几条链。 */
    public record DeleteResult(boolean fileRemoved, int linksRemoved) {
    }

    private final Store store;
    private final MediaDir mediaDir;
    private final ObjectMapper mapper = new ObjectMapper();

    public AssetService(Store store, MediaDir mediaDir) {
        this.store = store;
        this.mediaDir = mediaDir;
    }

    // ---------------- 登记 ----------------

    /**
     * 登记一份**已在媒体根下**的文件：探测字段 → 落库。
     *
     * @param relPath 相对 media 根的路径（两种布局并存，见 §3）
     * @param source  upload | generated | extracted
     * @param tags    逗号分隔标签，可空
     * @return 新资产 id
     */
    public long register(String relPath, String source, String name, String tags) {
        return register(relPath, source, name, tags, null);
    }

    /**
     * 登记内部实现；{@code knownSha} 非空时跳过重复摘要（上传路径已在写盘时算过）。
     * 双算一次 sha 对视频是数百 MB 的无谓 IO，故留此口子 —— 但对外仍是单一路径。
     */
    private long register(String relPath, String source, String name, String tags, String knownSha) {
        String ext = extOf(relPath);
        String kind = KIND_BY_EXT.get(ext);
        if (kind == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "不支持的扩展名：" + (ext.isEmpty() ? "(无)" : ext));
        }
        Path abs = mediaDir.resolveSafe(relPath);
        long size = 0L;
        String sha = knownSha;
        try {
            if (Files.isRegularFile(abs)) {
                size = Files.size(abs);
                if (sha == null) {
                    sha = sha256Of(abs);
                }
            } else {
                // 文件不在（或不可读）：仍登记记录，但 sha 留空 —— 空 sha 的资产**永不删文件**（§6 保守策略）
                log.warn("登记资产时文件不存在，sha 留空：{}", abs);
            }
        } catch (Exception e) {
            // 探测失败不该让调用方（封面/入料）整体失败；记 0 继续
            log.warn("资产字段探测失败，按空值登记：{}", abs, e);
        }
        int[] dims = dimensionsOf(abs, kind);
        long id = store.insertAsset(kind, source, name, relPath,
                MediaController.contentTypeOf(relPath).toString(), size, dims[0], dims[1],
                0d, sha == null ? "" : sha, tagsJson(tags));
        log.info("asset registered: id={} kind={} source={} rel={}", id, kind, source, relPath);
        return id;
    }

    // ---------------- 上传 ----------------

    /**
     * 上传一个新资产：白名单校验 → **流式**写临时文件同时算 sha256 → 内容寻址落盘 → 登记。
     *
     * <p>先写临时文件再改名，避免半截文件被登记成正式资产；同 sha 已存在时直接丢弃临时文件，
     * 天然去重（§3）。
     */
    public long upload(MultipartFile file, String name, String tags) throws IOException {
        if (file == null || file.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "未选择文件或文件为空");
        }
        String original = file.getOriginalFilename() == null ? "" : file.getOriginalFilename();
        String ext = extOf(original);
        if (!KIND_BY_EXT.containsKey(ext)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "不支持的扩展名：" + (ext.isEmpty() ? "(无)" : ext));
        }

        Path assetsDir = mediaDir.root().resolve("assets");
        Files.createDirectories(assetsDir);
        Path tmp = assetsDir.resolve(".tmp-" + UUID.randomUUID().toString().replace("-", "") + "." + ext);
        String sha;
        try {
            sha = writeAndDigest(file, tmp);
        } catch (IOException e) {
            Files.deleteIfExists(tmp);
            throw e;
        }

        String relPath = "assets/" + sha.substring(0, 2) + "/" + sha + "." + ext;
        Path target = mediaDir.resolveSafe(relPath);
        Files.createDirectories(target.getParent());
        if (Files.exists(target)) {
            // 同一份内容已在库里：只留一份，临时文件丢弃
            Files.deleteIfExists(tmp);
            log.info("asset upload deduped: {} already on disk", relPath);
        } else {
            moveInto(tmp, target);
        }
        String finalName = (name == null || name.isBlank()) ? original : name.trim();
        return register(relPath, "upload", finalName, tags, sha);
    }

    /** 流式落临时文件并同时喂 MessageDigest；返回 sha256 十六进制。 */
    private String writeAndDigest(MultipartFile file, Path tmp) throws IOException {
        MessageDigest md = newDigest();
        try (InputStream in = file.getInputStream(); OutputStream out = Files.newOutputStream(tmp)) {
            byte[] buf = new byte[BUFFER_BYTES];
            int n;
            while ((n = in.read(buf)) > 0) {
                md.update(buf, 0, n);
                out.write(buf, 0, n);
            }
        }
        return HexFormat.of().formatHex(md.digest());
    }

    /** 优先原子改名；跨卷等不支持原子移动时退化为普通移动（内容寻址下覆盖同内容无害）。 */
    private void moveInto(Path tmp, Path target) throws IOException {
        try {
            Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    // ---------------- 关联 ----------------

    /**
     * 资产 → 宿主（material | draft）关联，**幂等**：重复 link 不报错、不重复插行。
     *
     * @return true=本次真的插入了新行
     */
    public boolean link(long assetId, String ownerKind, long ownerId) {
        if (ownerKind == null || ownerKind.isBlank() || ownerId <= 0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "owner_kind / owner_id 非法");
        }
        if (store.hasAssetLink(assetId, ownerKind, ownerId)) {
            return false;
        }
        store.insertAssetLink(assetId, ownerKind, ownerId);
        return true;
    }

    // ---------------- 删除（docs/73 §6）----------------

    /**
     * 三态删除：① 解链 ② 删记录 ③ **仅当再无任何记录引用同 sha（且 sha 非空）**才删物理文件。
     */
    public DeleteResult delete(long id) {
        Store.AssetRow row = store.getAsset(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        int linksRemoved = store.deleteAssetLinksByAsset(id);
        store.deleteAsset(id);

        boolean fileRemoved = false;
        String sha = row.sha256();
        if (sha != null && !sha.isBlank() && store.assetsBySha(sha).isEmpty()) {
            try {
                fileRemoved = Files.deleteIfExists(mediaDir.resolveSafe(row.relPath()));
            } catch (Exception e) {
                // 文件删不掉不影响"记录已删"的事实：如实回 file_removed=false
                log.warn("资产物理文件删除失败（记录已删，文件保留）id={} rel={}", id, row.relPath(), e);
            }
        }
        log.info("asset deleted: id={} links_removed={} file_removed={}", id, linksRemoved, fileRemoved);
        return new DeleteResult(fileRemoved, linksRemoved);
    }

    // ---------------- 查询 ----------------

    public Optional<Store.AssetRow> get(long id) {
        return store.getAsset(id);
    }

    public List<Store.AssetRow> list(List<String> kinds, String q, int limit, int offset) {
        return store.listAssets(kinds, q, limit, offset);
    }

    public int count(List<String> kinds, String q) {
        return store.countAssets(kinds, q);
    }

    /** 记录 → 对外 DTO：补 url（逐段编码）与 links。 */
    public AssetDto toDto(Store.AssetRow row) {
        List<LinkDto> links = new ArrayList<>();
        for (Store.AssetLinkRow l : store.linksOf(row.id())) {
            links.add(new LinkDto(l.ownerKind(), l.ownerId()));
        }
        return new AssetDto(row.id(), row.kind(), row.source(), row.name(), row.relPath(),
                MediaController.mediaUrl(row.relPath()), row.mime(), row.sizeBytes(),
                row.width(), row.height(), row.durationSec(), row.sha256(),
                parseTags(row.tagsJson()), row.createdAt(), links);
    }

    // ---------------- helpers ----------------

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("JDK 缺少 SHA-256", e);
        }
    }

    /** 流式摘要：**绝不**整份读进内存（视频可达数百 MB，docs/73 §5）。 */
    private static String sha256Of(Path p) throws IOException {
        MessageDigest md = newDigest();
        try (InputStream in = Files.newInputStream(p)) {
            byte[] buf = new byte[BUFFER_BYTES];
            int n;
            while ((n = in.read(buf)) > 0) {
                md.update(buf, 0, n);
            }
        }
        return HexFormat.of().formatHex(md.digest());
    }

    /**
     * 宽高**尽力而为**（docs/73 §5）：png/jpg/gif/bmp 可读，webp/svg 读不到 → 记 0。
     * 只对 image 尝试，避免把视频整段喂给 ImageIO。不引入新依赖。
     */
    private static int[] dimensionsOf(Path p, String kind) {
        if (!"image".equals(kind) || !Files.isRegularFile(p)) {
            return new int[]{0, 0};
        }
        try {
            BufferedImage img = ImageIO.read(p.toFile());
            return img == null ? new int[]{0, 0} : new int[]{img.getWidth(), img.getHeight()};
        } catch (Exception e) {
            return new int[]{0, 0};
        }
    }

    private static String extOf(String filename) {
        int dot = filename == null ? -1 : filename.lastIndexOf('.');
        return dot < 0 ? "" : filename.substring(dot + 1).toLowerCase();
    }

    /** 逗号分隔标签 → JSON 数组字符串（落库格式固定为数组，前端不用再解析两种形状）。 */
    private String tagsJson(String tags) {
        List<String> list = new ArrayList<>();
        if (tags != null) {
            for (String t : tags.split(",")) {
                String v = t.trim();
                if (!v.isEmpty()) {
                    list.add(v);
                }
            }
        }
        try {
            return mapper.writeValueAsString(list);
        } catch (Exception e) {
            return "[]";
        }
    }

    private List<String> parseTags(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return mapper.readValue(json, new com.fasterxml.jackson.core.type.TypeReference<List<String>>() {
            });
        } catch (Exception e) {
            log.warn("tags_json 解析失败，按空标签返回：{}", json);
            return List.of();
        }
    }
}
