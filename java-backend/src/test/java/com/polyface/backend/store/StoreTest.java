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
}
