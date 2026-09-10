package com.polyface.backend.store;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 本地 SQLite 存储（本地优先：素材/稿件只在本机）。
 * 表：material(素材+结构化结果) / draft(各平台成稿)。
 */
@Component
public class Store {

    private static final Logger log = LoggerFactory.getLogger(Store.class);

    public record MaterialRow(long id, String rawText, String sourceKind, String title,
                              String coreMessage, String tone, String audience,
                              String factsJson, String createdAt) {
    }

    public record DraftRow(long id, long materialId, String platformCode, String platformName,
                           String briefJson, String payloadJson, String qaJson, String status,
                           String createdAt) {
    }

    /** 发布后录入的效果指标（FR-30）。 */
    public record EffectRow(long id, long draftId, int views, int likes, int favs, int comments,
                            String postedAt, String note, String createdAt) {
    }

    /** 创作者单行画像（FR-32，固定 id=1）。 */
    public record CreatorProfileRow(String brandVoice, String domain, String audience,
                                    String avoid, String updatedAt) {
    }

    /** 复盘建议日志（FR-31/33，可由自动聚合写入 + 人工接受/忽略）。 */
    public record RetroRow(long id, String kind, String platformCode, String domain,
                           String insight, String createdAt) {
    }

    private final String jdbcUrl;
    private final Path dbPath;
    private volatile boolean schemaReady = false;

    public Store(@Value("${polyface.data-dir:../data}") String dataDir) {
        Path dir = Path.of(dataDir).toAbsolutePath();
        this.dbPath = dir.resolve("polyface.db");
        this.jdbcUrl = "jdbc:sqlite:" + dbPath;
        log.info("SQLite store: {}", dbPath);
    }

    // ---------------- helpers ----------------
    private Connection open() throws java.sql.SQLException {
        if (!schemaReady) {
            synchronized (this) {
                if (!schemaReady) {
                    initSchema();
                    schemaReady = true;
                }
            }
        }
        return DriverManager.getConnection(jdbcUrl);
    }

    private void initSchema() throws java.sql.SQLException {
        try {
            Files.createDirectories(dbPath.getParent());
        } catch (Exception e) {
            throw new java.sql.SQLException("cannot create data dir: " + e.getMessage());
        }
        // 注意：sqlite-jdbc 单次 execute 仅执行首条语句，须逐条执行
        String[] statements = {
                "CREATE TABLE IF NOT EXISTS material ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "raw_text TEXT NOT NULL, source_kind TEXT, title TEXT,"
                        + "core_message TEXT, tone TEXT, audience TEXT, facts_json TEXT, created_at TEXT)",
                "CREATE TABLE IF NOT EXISTS draft ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "material_id INTEGER NOT NULL, platform_code TEXT NOT NULL, platform_name TEXT,"
                        + "brief_json TEXT, payload_json TEXT, qa_json TEXT, status TEXT, created_at TEXT)",
                // FR-30 发布后回填：播放/赞/藏/评 + 发布日期 + 备注
                "CREATE TABLE IF NOT EXISTS effect_metrics ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "draft_id INTEGER NOT NULL,"
                        + "views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,"
                        + "favs INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,"
                        + "posted_at TEXT, note TEXT, created_at TEXT)",
                // FR-32 创作者画像：单行(id=1)
                "CREATE TABLE IF NOT EXISTS creator_profile ("
                        + "id INTEGER PRIMARY KEY,"
                        + "brand_voice TEXT, domain TEXT, audience TEXT, avoid TEXT, updated_at TEXT)",
                // FR-31/33 复盘建议：自动聚合 + 人工接受可写入
                "CREATE TABLE IF NOT EXISTS retrospect_log ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "kind TEXT, platform_code TEXT, domain TEXT,"
                        + "insight TEXT NOT NULL, created_at TEXT)"
        };
        try (Connection c = DriverManager.getConnection(jdbcUrl); Statement st = c.createStatement()) {
            for (String sql : statements) {
                st.execute(sql);
            }
        }
        log.info("SQLite schema ready");
    }

    // ---------------- material ----------------
    public long insertMaterial(String rawText, String sourceKind, String title,
                               String coreMessage, String tone, String audience, String factsJson) {
        String sql = "INSERT INTO material(raw_text, source_kind, title, core_message, tone, audience, facts_json, created_at) "
                + "VALUES(?,?,?,?,?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, rawText);
            ps.setString(2, sourceKind);
            ps.setString(3, title);
            ps.setString(4, coreMessage);
            ps.setString(5, tone);
            ps.setString(6, audience);
            ps.setString(7, factsJson);
            ps.setString(8, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertMaterial failed", e);
        }
    }

    public Optional<MaterialRow> getMaterial(long id) {
        String sql = "SELECT id, raw_text, source_kind, title, core_message, tone, audience, facts_json, created_at "
                + "FROM material WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapMaterial(rs));
                }
            }
            return Optional.empty();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("getMaterial failed", e);
        }
    }

    public List<MaterialRow> listMaterials() {
        String sql = "SELECT id, raw_text, source_kind, title, core_message, tone, audience, facts_json, created_at "
                + "FROM material ORDER BY id DESC";
        List<MaterialRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
            while (rs.next()) {
                out.add(mapMaterial(rs));
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("listMaterials failed", e);
        }
        return out;
    }

    private MaterialRow mapMaterial(ResultSet rs) throws java.sql.SQLException {
        return new MaterialRow(
                rs.getLong("id"), rs.getString("raw_text"), rs.getString("source_kind"),
                rs.getString("title"), rs.getString("core_message"), rs.getString("tone"),
                rs.getString("audience"), rs.getString("facts_json"), rs.getString("created_at"));
    }

    // ---------------- draft ----------------
    public long insertDraft(long materialId, String platformCode, String platformName,
                            String briefJson, String payloadJson, String qaJson, String status) {
        String sql = "INSERT INTO draft(material_id, platform_code, platform_name, brief_json, payload_json, qa_json, status, created_at) "
                + "VALUES(?,?,?,?,?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setLong(1, materialId);
            ps.setString(2, platformCode);
            ps.setString(3, platformName);
            ps.setString(4, briefJson);
            ps.setString(5, payloadJson);
            ps.setString(6, qaJson);
            ps.setString(7, status);
            ps.setString(8, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertDraft failed", e);
        }
    }

    public List<DraftRow> draftsByMaterial(long materialId) {
        String sql = "SELECT id, material_id, platform_code, platform_name, brief_json, payload_json, qa_json, status, created_at "
                + "FROM draft WHERE material_id=? ORDER BY id";
        List<DraftRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, materialId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(new DraftRow(rs.getLong("id"), rs.getLong("material_id"),
                            rs.getString("platform_code"), rs.getString("platform_name"),
                            rs.getString("brief_json"), rs.getString("payload_json"),
                            rs.getString("qa_json"), rs.getString("status"), rs.getString("created_at")));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("draftsByMaterial failed", e);
        }
        return out;
    }

    public Optional<DraftRow> getDraft(long id) {
        String sql = "SELECT id, material_id, platform_code, platform_name, brief_json, payload_json, qa_json, status, created_at "
                + "FROM draft WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(new DraftRow(rs.getLong("id"), rs.getLong("material_id"),
                            rs.getString("platform_code"), rs.getString("platform_name"),
                            rs.getString("brief_json"), rs.getString("payload_json"),
                            rs.getString("qa_json"), rs.getString("status"), rs.getString("created_at")));
                }
            }
            return Optional.empty();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("getDraft failed", e);
        }
    }

    // ---------------- effect metrics (FR-30) ----------------
    /** 录入一条效果指标。一稿允许多条(滚动累计/分时段)。 */
    public long insertEffect(long draftId, int views, int likes, int favs, int comments,
                             String postedAt, String note) {
        String sql = "INSERT INTO effect_metrics(draft_id, views, likes, favs, comments, posted_at, note, created_at) "
                + "VALUES(?,?,?,?,?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setLong(1, draftId);
            ps.setInt(2, Math.max(0, views));
            ps.setInt(3, Math.max(0, likes));
            ps.setInt(4, Math.max(0, favs));
            ps.setInt(5, Math.max(0, comments));
            ps.setString(6, postedAt);
            ps.setString(7, note);
            ps.setString(8, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertEffect failed", e);
        }
    }

    /** 某稿的全部效果记录(按时间倒序)。 */
    public List<EffectRow> effectsByDraft(long draftId) {
        String sql = "SELECT id, draft_id, views, likes, favs, comments, posted_at, note, created_at "
                + "FROM effect_metrics WHERE draft_id=? ORDER BY id DESC";
        List<EffectRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, draftId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(mapEffect(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("effectsByDraft failed", e);
        }
        return out;
    }

    /** 全部效果(聚合用,按 draft_id ASC 便于聚合去重取最新一条)。 */
    public List<EffectRow> allEffects() {
        String sql = "SELECT id, draft_id, views, likes, favs, comments, posted_at, note, created_at "
                + "FROM effect_metrics ORDER BY draft_id, id";
        List<EffectRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
            while (rs.next()) {
                out.add(mapEffect(rs));
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("allEffects failed", e);
        }
        return out;
    }

    /** 某稿最新一条效果(返回最新值/累计视图)。 */
    public Optional<EffectRow> latestEffectForDraft(long draftId) {
        List<EffectRow> all = effectsByDraft(draftId);
        return all.isEmpty() ? Optional.empty() : Optional.of(all.get(0));
    }

    private EffectRow mapEffect(ResultSet rs) throws java.sql.SQLException {
        return new EffectRow(
                rs.getLong("id"), rs.getLong("draft_id"),
                rs.getInt("views"), rs.getInt("likes"),
                rs.getInt("favs"), rs.getInt("comments"),
                rs.getString("posted_at"), rs.getString("note"),
                rs.getString("created_at"));
    }

    // ---------------- creator profile (FR-32) ----------------
    /** 单行 upsert：固定 id=1。 */
    public void upsertProfile(String brandVoice, String domain, String audience, String avoid) {
        // 先确保行存在
        String ensure = "INSERT OR IGNORE INTO creator_profile(id, brand_voice, domain, audience, avoid, updated_at) "
                + "VALUES(1, '', '', '', '', ?)";
        String update = "UPDATE creator_profile SET brand_voice=?, domain=?, audience=?, avoid=?, updated_at=? WHERE id=1";
        String now = LocalDateTime.now().toString();
        try (Connection c = open()) {
            try (PreparedStatement ps = c.prepareStatement(ensure)) {
                ps.setString(1, now);
                ps.executeUpdate();
            }
            try (PreparedStatement ps = c.prepareStatement(update)) {
                ps.setString(1, brandVoice == null ? "" : brandVoice);
                ps.setString(2, domain == null ? "" : domain);
                ps.setString(3, audience == null ? "" : audience);
                ps.setString(4, avoid == null ? "" : avoid);
                ps.setString(5, now);
                ps.executeUpdate();
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("upsertProfile failed", e);
        }
    }

    public Optional<CreatorProfileRow> getProfile() {
        String sql = "SELECT brand_voice, domain, audience, avoid, updated_at FROM creator_profile WHERE id=1";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
            if (rs.next()) {
                return Optional.of(new CreatorProfileRow(
                        rs.getString("brand_voice"), rs.getString("domain"),
                        rs.getString("audience"), rs.getString("avoid"),
                        rs.getString("updated_at")));
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("getProfile failed", e);
        }
        return Optional.empty();
    }

    // ---------------- retrospect log (FR-31/33) ----------------
    public long insertRetro(String kind, String platformCode, String domain, String insight) {
        String sql = "INSERT INTO retrospect_log(kind, platform_code, domain, insight, created_at) "
                + "VALUES(?,?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, kind == null ? "neutral" : kind);
            ps.setString(2, platformCode);
            ps.setString(3, domain);
            ps.setString(4, insight);
            ps.setString(5, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertRetro failed", e);
        }
    }

    public List<RetroRow> listRetros(int limit) {
        String sql = "SELECT id, kind, platform_code, domain, insight, created_at FROM retrospect_log "
                + "ORDER BY id DESC LIMIT ?";
        List<RetroRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setInt(1, Math.max(1, limit));
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(new RetroRow(rs.getLong("id"),
                            rs.getString("kind"), rs.getString("platform_code"),
                            rs.getString("domain"), rs.getString("insight"),
                            rs.getString("created_at")));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("listRetros failed", e);
        }
        return out;
    }
}
