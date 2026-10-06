package com.polyface.backend.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.web.server.ResponseStatusException;

import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 资产登记/删除语义（M7-1，docs/73 §3/§5/§6）。
 *
 * <p>用真实 SQLite + 真实媒体目录（临时 data-dir），因为这一层的正确性恰恰在
 * "文件与记录是否对得上"，mock 掉就什么都没验证。
 */
@SpringBootTest
@TestPropertySource(properties = "polyface.data-dir=target/test-data-asset")
class AssetServiceTest {

    @Autowired
    private AssetService assets;

    @Autowired
    private Store store;

    @Autowired
    private MediaDir mediaDir;

    private static MockMultipartFile mp(String filename, byte[] content) {
        return new MockMultipartFile("file", filename, "application/octet-stream", content);
    }

    private static String sha256(byte[] data) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
    }

    /** 每次跑用随机内容：避免上一轮的产物让"盘上只有一份"的断言失真。 */
    private static byte[] freshBytes(String tag) {
        return (tag + "-" + UUID.randomUUID()).getBytes();
    }

    private static long countFiles(Path dir) throws Exception {
        if (!Files.isDirectory(dir)) {
            return 0L;
        }
        try (var walk = Files.walk(dir)) {
            return walk.filter(Files::isRegularFile).count();
        }
    }

    /** ① 内容寻址：同 sha 只存一份，但允许两条记录（docs/73 §3 去重语义）。 */
    @Test
    void uploadIsContentAddressedAndDeduped() throws Exception {
        byte[] bytes = freshBytes("dedupe");
        long id1 = assets.upload(mp("甲.png", bytes), "甲", "标签A");
        long id2 = assets.upload(mp("乙.png", bytes), "乙", null);

        assertThat(id2).isNotEqualTo(id1);
        String sha = sha256(bytes);
        String expectedRel = "assets/" + sha.substring(0, 2) + "/" + sha + ".png";

        Store.AssetRow r1 = assets.get(id1).orElseThrow();
        Store.AssetRow r2 = assets.get(id2).orElseThrow();
        assertThat(r1.sha256()).isEqualTo(sha);
        assertThat(r1.relPath()).isEqualTo(expectedRel);
        // 两条记录指同一份文件 = 天然去重
        assertThat(r2.relPath()).isEqualTo(expectedRel);
        assertThat(r1.kind()).isEqualTo("image");
        assertThat(r1.source()).isEqualTo("upload");
        assertThat(r1.mime()).isEqualTo("image/png");
        assertThat(r1.sizeBytes()).isEqualTo(bytes.length);
        // 盘上该 sha 只落一份；临时文件不留痕
        assertThat(Files.exists(mediaDir.root().resolve(expectedRel))).isTrue();
        try (var walk = Files.walk(mediaDir.root().resolve("assets"))) {
            assertThat(walk.filter(Files::isRegularFile)
                    .filter(p -> p.getFileName().toString().startsWith(sha))
                    .count()).isEqualTo(1L);
        }
        assertThat(assets.toDto(r1).tags()).containsExactly("标签A");
    }

    /** ② 删除三态（docs/73 §6）：唯一引用删文件 / 尚有引用保留 / sha 为空永不删。 */
    @Test
    void deleteSemanticsThreeStates() throws Exception {
        // ① 唯一引用 + 有链 → 解链、删记录、删文件
        long idA = assets.upload(mp("a.mp3", freshBytes("alpha")), "音频A", null);
        assets.link(idA, "draft", 42L);
        Path fileA = mediaDir.root().resolve(assets.get(idA).orElseThrow().relPath());
        assertThat(Files.exists(fileA)).isTrue();

        AssetService.DeleteResult r1 = assets.delete(idA);
        assertThat(r1.linksRemoved()).isEqualTo(1);
        assertThat(r1.fileRemoved()).isTrue();
        assertThat(Files.exists(fileA)).isFalse();
        assertThat(assets.get(idA)).isEmpty();

        // ② 同 sha 还有别的记录 → 文件保留；最后一条记录删掉才删文件
        byte[] shared = freshBytes("beta");
        long idB1 = assets.upload(mp("b1.mp3", shared), "B1", null);
        long idB2 = assets.upload(mp("b2.mp3", shared), "B2", null);
        Path fileB = mediaDir.root().resolve(assets.get(idB1).orElseThrow().relPath());

        AssetService.DeleteResult r2 = assets.delete(idB1);
        assertThat(r2.fileRemoved()).isFalse();
        assertThat(Files.exists(fileB)).isTrue();

        AssetService.DeleteResult r3 = assets.delete(idB2);
        assertThat(r3.fileRemoved()).isTrue();
        assertThat(Files.exists(fileB)).isFalse();

        // ③ sha 算不出（空串）→ 保守：只删记录，**绝不**动文件
        Path legacy = mediaDir.root().resolve("legacy-video.mp4");
        Files.write(legacy, freshBytes("legacy"));
        long idC = store.insertAsset("video", "upload", "旧入料", "legacy-video.mp4", "video/mp4",
                Files.size(legacy), 0, 0, 0d, "", "[]");
        AssetService.DeleteResult r4 = assets.delete(idC);
        assertThat(r4.fileRemoved()).isFalse();
        assertThat(Files.exists(legacy)).isTrue();

        // 不存在的资产 → 404（不是静默成功）
        ResponseStatusException ex = assertThrows(ResponseStatusException.class, () -> assets.delete(999_999L));
        assertThat(ex.getStatusCode().value()).isEqualTo(404);
    }

    /** ③ 宽高尽力而为：png 探得到，svg 探不到记 0（docs/73 §5）。 */
    @Test
    void dimensionsAreBestEffort() throws Exception {
        Path dir = mediaDir.root().resolve("probe");
        Files.createDirectories(dir);
        Path png = dir.resolve("pic.png");
        ImageIO.write(new BufferedImage(12, 7, BufferedImage.TYPE_INT_RGB), "png", png.toFile());

        Store.AssetRow row = assets.get(assets.register("probe/pic.png", "generated", "图", null)).orElseThrow();
        assertThat(row.width()).isEqualTo(12);
        assertThat(row.height()).isEqualTo(7);
        assertThat(row.kind()).isEqualTo("image");
        assertThat(row.mime()).isEqualTo("image/png");
        assertThat(row.durationSec()).isZero();

        Path svg = dir.resolve("icon.svg");
        Files.writeString(svg, "<svg xmlns=\"http://www.w3.org/2000/svg\"/>");
        Store.AssetRow svgRow = assets.get(assets.register("probe/icon.svg", "generated", "图标", null)).orElseThrow();
        assertThat(svgRow.kind()).isEqualTo("image");
        assertThat(svgRow.width()).isZero();
        assertThat(svgRow.height()).isZero();
    }

    /** ④ 上传白名单：白名单外扩展名一律 400，且不留任何文件。 */
    @Test
    void uploadRejectsExtensionOutsideWhitelist() throws Exception {
        Path assetsDir = mediaDir.root().resolve("assets");
        long before = countFiles(assetsDir);

        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> assets.upload(mp("payload.exe", freshBytes("exe")), null, null));
        assertThat(ex.getStatusCode().value()).isEqualTo(400);

        ResponseStatusException empty = assertThrows(ResponseStatusException.class,
                () -> assets.upload(mp("empty.png", new byte[0]), null, null));
        assertThat(empty.getStatusCode().value()).isEqualTo(400);

        assertThat(countFiles(assetsDir)).isEqualTo(before);
    }

    /** ⑤ link 幂等：同 (asset, owner) 重复 link 不重复插行；不同 owner 各记一条。 */
    @Test
    void linkIsIdempotent() throws Exception {
        long id = assets.upload(mp("c.png", freshBytes("c")), "C", null);
        assertThat(assets.link(id, "draft", 1L)).isTrue();
        assertThat(assets.link(id, "draft", 1L)).isFalse();
        assertThat(store.linksOf(id)).hasSize(1);

        assertThat(assets.link(id, "material", 5L)).isTrue();
        assertThat(store.linksOf(id)).hasSize(2);

        ResponseStatusException bad = assertThrows(ResponseStatusException.class,
                () -> assets.link(id, "draft", 0L));
        assertThat(bad.getStatusCode().value()).isEqualTo(400);
    }

    /** ⑥ 登记未知扩展名 → 400（避免库里出现无法托管、无 kind 的记录）。 */
    @Test
    void registerRejectsUnknownExtension() {
        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> assets.register("note.txt", "generated", "笔记", null));
        assertThat(ex.getStatusCode().value()).isEqualTo(400);
    }

    /** ⑦ 查询：kind 多值 + name 模糊 + 分页。 */
    @Test
    void listFiltersByKindAndName() throws Exception {
        String tag = UUID.randomUUID().toString().substring(0, 8);
        long img = assets.upload(mp("q1.png", freshBytes("q1")), "查询图-" + tag, null);
        long aud = assets.upload(mp("q2.mp3", freshBytes("q2")), "查询音-" + tag, null);

        List<Store.AssetRow> images = assets.list(List.of("image"), tag, 50, 0);
        assertThat(images).extracting(Store.AssetRow::id).contains(img).doesNotContain(aud);
        assertThat(assets.count(List.of("image"), tag)).isEqualTo(images.size());

        assertThat(assets.list(List.of("image", "audio"), tag, 50, 0)).hasSize(2);
        assertThat(assets.list(List.of(), tag, 1, 1)).hasSize(1);
    }
}
