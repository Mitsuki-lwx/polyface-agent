package com.polyface.backend.store;

import static org.assertj.core.api.Assertions.assertThat;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

import org.junit.jupiter.api.Test;

/**
 * 能力测试：sqlite-jdbc 的 FTS5 **对中文到底能用到什么程度**。
 *
 * <p>为什么要单独测：这是素材全文检索（`openspec/changes/material-keywords-and-search`
 * 任务 0）的前提，而它取决于**打包进 jar 的 SQLite 编译选项与分词器**，与我们的代码无关 ✗。
 *
 * <p>2026-11-07 实测结论（本机 sqlite-jdbc 3.46.1.0）：
 * <ul>
 *   <li>FTS5 **可用**，建表不报错 ✓</li>
 *   <li>默认分词器 unicode61 把整句当一个 token → 搜「复用」**命中不了** ✗</li>
 *   <li>tokenize=trigram 能搜中文子串 ✓，但要求查询 **≥3 个字符** → 「复用」「周报」
 *       这种两字词**命中不了** ✗</li>
 * </ul>
 * 结论：**中文检索退回 LIKE**（功能正确、性能差一档），不引入自定义分词器 ✗。
 * 这条测试把结论钉住 —— 哪天驱动升级支持了更好的分词，这里会先红，提醒重新评估。
 */
class SqliteFts5CapabilityTest {

    @Test
    void fts5_is_available() throws Exception {
        try (Connection c = DriverManager.getConnection("jdbc:sqlite::memory:");
             Statement s = c.createStatement()) {
            s.execute("CREATE VIRTUAL TABLE probe USING fts5(body)");
            assertThat(true).as("FTS5 建表可用").isTrue();
        }
    }

    @Test
    void default_tokenizer_cannot_match_chinese_substring() throws Exception {
        try (Connection c = DriverManager.getConnection("jdbc:sqlite::memory:");
             Statement s = c.createStatement()) {
            s.execute("CREATE VIRTUAL TABLE probe USING fts5(body)");
            s.execute("INSERT INTO probe(body) VALUES ('内容复用不是一稿多投')");
            try (ResultSet r = s.executeQuery("SELECT COUNT(*) FROM probe WHERE probe MATCH '复用'")) {
                r.next();
                assertThat(r.getInt(1))
                        .as("默认分词器下整句是一个 token，两字词搜不到 —— 若此处变成 1，说明分词变了，需重新评估")
                        .isZero();
            }
        }
    }

    @Test
    void trigram_tokenizer_matches_three_chars_but_not_two() throws Exception {
        try (Connection c = DriverManager.getConnection("jdbc:sqlite::memory:");
             Statement s = c.createStatement()) {
            s.execute("CREATE VIRTUAL TABLE probe USING fts5(body, tokenize='trigram')");
            s.execute("INSERT INTO probe(body) VALUES ('内容复用不是一稿多投')");

            try (ResultSet r = s.executeQuery("SELECT COUNT(*) FROM probe WHERE probe MATCH '一稿多'")) {
                r.next();
                assertThat(r.getInt(1)).as("trigram 应能搜到 3 字子串").isEqualTo(1);
            }
            try (ResultSet r = s.executeQuery("SELECT COUNT(*) FROM probe WHERE probe MATCH '复用'")) {
                r.next();
                assertThat(r.getInt(1))
                        .as("trigram 最小 3 字 —— 两字词命中不了，这正是不能用 FTS5 做中文检索的原因")
                        .isZero();
            }
        }
    }
}
