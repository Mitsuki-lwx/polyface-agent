package com.polyface.backend.job;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 成片产物的**血缘**（`docs/checklist_b1.md` §8-4：产物进素材库，血缘挂到源素材/成稿）。
 *
 * <p>为什么单独测这一条：成片登记走的是 {@code registerFilmOutputs}，而它原先只在
 * 「输入本身就是资产」时才建链 —— 图文成片的输入是**剪辑单**（成稿里取出来的），
 * 于是 `asset_link` **一行都不写**，成片在素材库里是断线的（界面"已用于 N 处"永远是 0，
 * 从成稿也查不到它出过什么片）。光测那个 helper 抓不到"忘了接线"，所以这里走真实入口。
 */
@SpringBootTest
@TestPropertySource(properties = {
        "polyface.data-dir=target/test-data-b1-lineage",
        "polyface.media.dir=target/test-data-b1-lineage/media",
})
class FilmLineageTest {

    @Autowired
    private Store store;

    @Autowired
    private RoughcutRunner runner;

    @Autowired
    private MediaDir mediaDir;

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void filmIsLinkedBackToItsDraft() throws Exception {
        long materialId = store.insertMaterial("素材", "长文", "B1 血缘", "核心", "理性", "受众", "[]");
        long draftId = store.insertDraft(materialId, "douyin", "抖音", "{}", "{}", "{}", "qa_passed");

        // 产物与剪辑单都放在媒体根下 —— `rel()` 是相对媒体根算的
        Path jobDir = mediaDir.root().resolve("roughcut/job-lineage");
        Files.createDirectories(jobDir);
        Path out = jobDir.resolve("film.mp4");
        Files.write(out, new byte[4096]);
        Path plan = jobDir.resolve("plan.json");
        com.fasterxml.jackson.databind.node.ObjectNode root = mapper.createObjectNode();
        root.putObject("source").put("material_id", materialId).put("platform", "douyin");
        Files.writeString(plan, root.toString(), StandardCharsets.UTF_8);

        runner.registerFilmOutputs(999_999L, out, jobDir.resolve("film.srt"), plan);

        Store.AssetRow film = store.listAssets(List.of("video"), null, 50, 0).stream()
                .filter(a -> a.relPath().endsWith("roughcut/job-lineage/film.mp4"))
                .findFirst().orElseThrow(() -> new AssertionError("成片没进素材库"));
        assertThat(store.linksOf(film.id()))
                .as("成片应当挂回它来自的那份成稿")
                .anyMatch(l -> "draft".equals(l.ownerKind()) && l.ownerId() == draftId);
    }

    @Test
    void missingSourceInPlanIsHarmless() throws Exception {
        // 老剪辑单没有 `source`，或成稿已被删 —— **不能因此让成片登记失败**（血缘是附加信息）
        Path jobDir = mediaDir.root().resolve("roughcut/job-nosource");
        Files.createDirectories(jobDir);
        Path out = jobDir.resolve("film.mp4");
        Files.write(out, new byte[4096]);
        Path plan = jobDir.resolve("plan.json");
        Files.writeString(plan, "{\"scenes\":[]}", StandardCharsets.UTF_8);

        runner.registerFilmOutputs(999_998L, out, jobDir.resolve("film.srt"), plan);

        assertThat(store.listAssets(List.of("video"), null, 50, 0))
                .anyMatch(a -> a.relPath().endsWith("roughcut/job-nosource/film.mp4"));
    }
}
