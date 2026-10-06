package com.polyface.backend.store;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Path;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Store(SQLite) 本地落库冒烟测试。 */
class StoreTest {

    @TempDir
    Path tempDir;

    private Store newStore() {
        return new Store(tempDir.toString());
    }

    @Test
    void insertAndReadMaterial() {
        Store store = newStore();
        long id = store.insertMaterial("我2023年裸辞做自由职业，靠写作月入0到3万。",
                "长文", "自由职业复盘", "裸辞后靠写作月入3万",
                "理性干货", "想转自由职业的人", "[]");
        assertThat(id).isGreaterThan(0);

        var row = store.getMaterial(id);
        assertThat(row).isPresent();
        assertThat(row.get().coreMessage()).isEqualTo("裸辞后靠写作月入3万");
        assertThat(store.listMaterials()).hasSize(1);
    }

    @Test
    void insertAndListDraftsByMaterial() {
        Store store = newStore();
        long mid = store.insertMaterial("素材", "长文", null, "核心", "tone", "aud", "[]");
        long d1 = store.insertDraft(mid, "xhs", "小红书", "{}", "{}", "{}", "qa_passed");
        long d2 = store.insertDraft(mid, "douyin", "抖音", "{}", "{}", "{}", "needs_review");

        assertThat(d1).isNotEqualTo(d2);
        List<Store.DraftRow> drafts = store.draftsByMaterial(mid);
        assertThat(drafts).hasSize(2);
        assertThat(drafts.get(0).platformCode()).isEqualTo("xhs");

        var one = store.getDraft(d1);
        assertThat(one).isPresent();
        assertThat(one.get().status()).isEqualTo("qa_passed");
        assertThat(store.getDraft(99999)).isEmpty();
        assertThat(store.getMaterial(99999)).isEmpty();
    }

    @Test
    void effectMetricsInsertAndLatest() {
        Store store = newStore();
        long mid = store.insertMaterial("素材", "长文", null, "core", "tone", "aud", "[]");
        long did = store.insertDraft(mid, "xhs", "小红书", "{}", "{}", "{}", "qa_passed");

        long e1 = store.insertEffect(did, 100, 5, 3, 1, "2026-09-10", "首日");
        long e2 = store.insertEffect(did, 500, 30, 20, 8, "2026-09-11", "次日");
        assertThat(e1).isGreaterThan(0);
        assertThat(e2).isGreaterThan(e1);

        var latest = store.latestEffectForDraft(did);
        assertThat(latest).isPresent();
        assertThat(latest.get().views()).isEqualTo(500);
        assertThat(latest.get().id()).isEqualTo(e2);

        var all = store.effectsByDraft(did);
        assertThat(all).hasSize(2);
        assertThat(all.get(0).id()).isEqualTo(e2); // 倒序

        // allEffects 聚合
        assertThat(store.allEffects()).hasSize(2);

        // 负数会被夹到 0
        long e3 = store.insertEffect(did, -10, -5, 0, 0, null, null);
        assertThat(store.effectsByDraft(did).get(0).views()).isEqualTo(0);
        assertThat(e3).isGreaterThan(0);
    }

    @Test
    void creatorProfileUpsert() {
        Store store = newStore();
        // 初始无画像
        assertThat(store.getProfile()).isEmpty();

        store.upsertProfile("理性干货", "自由职业", "职场人", "不要AI味");
        var p1 = store.getProfile();
        assertThat(p1).isPresent();
        assertThat(p1.get().brandVoice()).isEqualTo("理性干货");
        assertThat(p1.get().domain()).isEqualTo("自由职业");
        assertThat(p1.get().audience()).isEqualTo("职场人");
        assertThat(p1.get().avoid()).isEqualTo("不要AI味");
        assertThat(p1.get().updatedAt()).isNotBlank();

        // 二次 upsert 覆盖
        store.upsertProfile("真诚种草", "母婴", "宝妈", "");
        var p2 = store.getProfile();
        assertThat(p2.get().brandVoice()).isEqualTo("真诚种草");
        assertThat(p2.get().domain()).isEqualTo("母婴");
        assertThat(p2.get().avoid()).isEqualTo(""); // 空字符串显式保留
        // updated_at 必须变化
        assertThat(p2.get().updatedAt()).isNotEqualTo(p1.get().updatedAt());
    }

    @Test
    void retrospectLogInsertAndList() {
        Store store = newStore();
        assertThat(store.listRetros(10)).isEmpty();

        long r1 = store.insertRetro("good", "douyin", null, "短钩子效果最好");
        long r2 = store.insertRetro("bad", "xhs", null, "长文首屏要加图");
        assertThat(r1).isLessThan(r2);

        var list = store.listRetros(10);
        assertThat(list).hasSize(2);
        assertThat(list.get(0).kind()).isEqualTo("bad"); // 倒序
        assertThat(list.get(0).insight()).isEqualTo("长文首屏要加图");

        // limit 生效
        assertThat(store.listRetros(1)).hasSize(1);

        // null kind 落到 neutral
        store.insertRetro(null, "zhihu", null, "中性建议");
        assertThat(store.listRetros(10).get(0).kind()).isEqualTo("neutral");
    }

    // ==================== M5: 模板管理（FR-62 / FR-64） ====================

    @Test
    void templateCrudAndVersionBump() {
        Store store = newStore();
        long id = store.insertTemplate("content", "我的开场", "直接", "大家好", "[\"先结论\",\"给证据\"]",
                "关注我", "短标签", "[\"不要AI味\"]", false, null, 1);
        assertThat(id).isGreaterThan(0);

        var t = store.getTemplate(id);
        assertThat(t).isPresent();
        assertThat(t.get().name()).isEqualTo("我的开场");
        assertThat(t.get().structureJson()).contains("先结论");
        assertThat(t.get().version()).isEqualTo(1);
        assertThat(t.get().builtin()).isFalse();

        // 编辑 → version +1
        boolean ok = store.updateTemplate(id, "我的开场2", "更直接", "你好", "[]", "", "", "[]");
        assertThat(ok).isTrue();
        var t2 = store.getTemplate(id);
        assertThat(t2.get().version()).isEqualTo(2);
        assertThat(t2.get().name()).isEqualTo("我的开场2");
        assertThat(t2.get().createdAt()).isEqualTo(t.get().createdAt()); // created 不变

        // 不存在 id 更新返回 false
        assertThat(store.updateTemplate(99999, "x", "", "", "[]", "", "", "[]")).isFalse();
        assertThat(store.getTemplate(99999)).isEmpty();

        // 删除
        assertThat(store.deleteTemplate(id)).isTrue();
        assertThat(store.getTemplate(id)).isEmpty();
    }

    @Test
    void templateListOrderAndFindByName() {
        Store store = newStore();
        long a = store.insertTemplate("content", "AAA", "", "", "[]", "", "", "[]", false, null, 1);
        long b = store.insertTemplate("content", "BBB", "", "", "[]", "", "", "[]", true, null, 1);
        long c = store.insertTemplate("content", "CCC", "", "", "[]", "", "", "[]", false, null, 1);

        var all = store.listTemplates(null);
        assertThat(all).hasSize(3);
        // 内置在前
        assertThat(all.get(0).id()).isEqualTo(b);
        assertThat(all.get(0).builtin()).isTrue();
        assertThat(all.get(1).id()).isEqualTo(a);
        assertThat(all.get(2).id()).isEqualTo(c);

        // kind 过滤
        assertThat(store.listTemplates("content")).hasSize(3);
        assertThat(store.listTemplates("clip")).isEmpty();

        // 同名查找
        assertThat(store.findTemplateByName("BBB")).isPresent();
        assertThat(store.findTemplateByName("ZZZ")).isEmpty();

        // 内置计数
        assertThat(store.countBuiltinTemplates()).isEqualTo(1);
    }

    @Test
    void templateOverwriteByNameOnlyAffectsMine() {
        Store store = newStore();
        // 我的模板：可被覆盖
        store.insertTemplate("content", "同名", "旧声音", "", "[]", "", "", "[]", false, null, 1);
        boolean ok = store.overwriteTemplateByName("同名", "新声音", "", "[]", "", "", "[]");
        assertThat(ok).isTrue();
        var mine = store.findTemplateByName("同名").orElseThrow();
        assertThat(mine.voice()).isEqualTo("新声音");
        assertThat(mine.version()).isEqualTo(2);

        // 内置模板：overwrite 不生效（红线：内置只读）
        long bId = store.insertTemplate("content", "内置名", "原生", "", "[]", "", "", "[]", true, null, 1);
        boolean ok2 = store.overwriteTemplateByName("内置名", "被改", "", "[]", "", "", "[]");
        assertThat(ok2).isFalse();
        var builtin = store.getTemplate(bId).orElseThrow();
        assertThat(builtin.voice()).isEqualTo("原生"); // 未被改动
        assertThat(builtin.version()).isEqualTo(1);
    }

    @Test
    void draftStoresTemplateVersion() {
        Store store = newStore();
        long mid = store.insertMaterial("素材", "长文", null, "core", "tone", "aud", "[]");
        long tplId = store.insertTemplate("content", "模板A", "", "", "[]", "", "", "[]", false, null, 1);
        store.updateTemplate(tplId, "模板A", "", "", "[]", "", "", "[]"); // version -> 2

        long d1 = store.insertDraft(mid, "xhs", "小红书", "{}", "{}", "{}", "qa_passed", tplId, 2);
        long d2 = store.insertDraft(mid, "douyin", "抖音", "{}", "{}", "{}", "qa_passed"); // 旧签名 → null

        var row1 = store.getDraft(d1).orElseThrow();
        assertThat(row1.templateId()).isEqualTo(tplId);
        assertThat(row1.templateVersion()).isEqualTo(2);

        var row2 = store.getDraft(d2).orElseThrow();
        assertThat(row2.templateId()).isNull();
        assertThat(row2.templateVersion()).isNull();
    }

    @Test
    void schemaMigrationIsIdempotent() {
        // 同一 data-dir 二次构造 Store（触发二次 initSchema）不应抛异常
        Store first = newStore();
        first.insertTemplate("content", "seed-check", "", "", "[]", "", "", "[]",
                true, null, 1, "draft", "学习来源");
        Store second = newStore(); // 再次 initSchema：template 表已存在、status/source_note 列已存在
        assertThat(second.countBuiltinTemplates()).isEqualTo(1);
        assertThat(second.getProfile()).isEmpty(); // 其他表无副作用
        // 二次迁移后 FR-63 字段未丢失
        var row = second.listTemplates(null).get(0);
        assertThat(row.status()).isEqualTo("draft");
        assertThat(row.sourceNote()).isEqualTo("学习来源");
    }

    // ==================== FR-63: 模板状态与启用 ====================

    @Test
    void templateDefaultsToActive() {
        Store store = newStore();
        long id = store.insertTemplate("content", "默认态", "", "", "[]", "", "", "[]",
                false, null, 1);
        var row = store.getTemplate(id).orElseThrow();
        assertThat(row.status()).isEqualTo("active");
        assertThat(row.sourceNote()).isNull();
    }

    @Test
    void templateStatusFilter() {
        Store store = newStore();
        long activeId = store.insertTemplate("content", "已启用", "", "", "[]", "", "", "[]",
                false, null, 1);
        long draftId = store.insertTemplate("content", "草稿", "", "", "[]", "", "", "[]",
                false, null, 1, "draft", "示例备注");

        // 不过滤 → 全部
        assertThat(store.listTemplates(null)).hasSize(2);
        // status=active
        assertThat(store.listTemplates(null, "active"))
                .extracting(Store.TemplateRow::id).containsExactly(activeId);
        // status=draft
        assertThat(store.listTemplates(null, "draft"))
                .extracting(Store.TemplateRow::id).containsExactly(draftId);

        var draft = store.getTemplate(draftId).orElseThrow();
        assertThat(draft.status()).isEqualTo("draft");
        assertThat(draft.sourceNote()).isEqualTo("示例备注");
    }

    @Test
    void activateDraftTemplate() {
        Store store = newStore();
        long id = store.insertTemplate("content", "草稿转正", "", "", "[]", "", "", "[]",
                false, null, 1, "draft", null);
        assertThat(store.listTemplates(null, "active")).isEmpty();

        assertThat(store.activateTemplate(id)).isTrue();
        assertThat(store.getTemplate(id).orElseThrow().status()).isEqualTo("active");
        assertThat(store.listTemplates(null, "draft")).isEmpty();
        assertThat(store.listTemplates(null, "active")).hasSize(1);

        // 幂等：已 active 再调用返回 false（无行被更新）
        assertThat(store.activateTemplate(id)).isFalse();
        // 不存在
        assertThat(store.activateTemplate(99999)).isFalse();
    }

    // ==================== M7-1: 统一资产库（asset / asset_link） ====================

    @Test
    void assetCrudFiltersAndLinks() {
        Store store = newStore();
        long img = store.insertAsset("image", "generated", "封面A", "covers/a/cover.png",
                "image/png", 123L, 1080, 1440, 0d, "sha-a", "[\"封面\"]");
        long aud = store.insertAsset("audio", "upload", "配乐", "assets/aa/sha-b.mp3",
                "audio/mpeg", 456L, 0, 0, 0d, "sha-b", "[]");
        long vid = store.insertAsset("video", "upload", "口播", "abc.mp4",
                "video/mp4", 789L, 0, 0, 0d, "sha-c", "[]");

        var row = store.getAsset(img).orElseThrow();
        assertThat(row.kind()).isEqualTo("image");
        assertThat(row.source()).isEqualTo("generated");
        assertThat(row.relPath()).isEqualTo("covers/a/cover.png");
        assertThat(row.sizeBytes()).isEqualTo(123L);
        assertThat(row.width()).isEqualTo(1080);
        assertThat(row.tagsJson()).isEqualTo("[\"封面\"]");
        assertThat(row.createdAt()).isNotBlank();

        // kind 多值 + name 模糊 + 分页（最新在前）
        assertThat(store.listAssets(List.of("audio", "video"), null, 50, 0))
                .extracting(Store.AssetRow::id).containsExactly(vid, aud);
        assertThat(store.listAssets(List.of(), "封面", 50, 0))
                .extracting(Store.AssetRow::id).containsExactly(img);
        assertThat(store.listAssets(null, null, 1, 1))
                .extracting(Store.AssetRow::id).containsExactly(aud);
        assertThat(store.countAssets(List.of("audio", "video"), null)).isEqualTo(2);
        assertThat(store.countAssets(null, "封面")).isEqualTo(1);

        // sha 计数（删除语义的判据）
        assertThat(store.assetsBySha("sha-b")).extracting(Store.AssetRow::id).containsExactly(aud);
        assertThat(store.assetsBySha("")).isEmpty();
        assertThat(store.assetsBySha(null)).isEmpty();

        // link：幂等判定 + 解链计数
        long linkId = store.insertAssetLink(img, "draft", 1L);
        assertThat(linkId).isPositive();
        assertThat(store.hasAssetLink(img, "draft", 1L)).isTrue();
        assertThat(store.hasAssetLink(img, "draft", 2L)).isFalse();
        assertThat(store.hasAssetLink(img, "material", 1L)).isFalse();
        store.insertAssetLink(img, "material", 5L);
        assertThat(store.linksOf(img)).hasSize(2);
        assertThat(store.linksOf(aud)).isEmpty();
        assertThat(store.deleteAssetLinksByAsset(img)).isEqualTo(2);
        assertThat(store.linksOf(img)).isEmpty();

        assertThat(store.deleteAsset(img)).isTrue();
        assertThat(store.getAsset(img)).isEmpty();
        assertThat(store.deleteAsset(img)).isFalse();
    }
}
