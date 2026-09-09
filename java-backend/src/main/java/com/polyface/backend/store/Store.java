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
                        + "brief_json TEXT, payload_json TEXT, qa_json TEXT, status TEXT, created_at TEXT)"
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
}
