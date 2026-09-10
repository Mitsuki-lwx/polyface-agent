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
}
