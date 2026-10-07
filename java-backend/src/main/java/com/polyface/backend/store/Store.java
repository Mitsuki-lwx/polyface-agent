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
                              String factsJson, String createdAt,
                              boolean factsConfirmed, String factsConfirmedAt) {
    }

    public record DraftRow(long id, long materialId, String platformCode, String platformName,
                           String briefJson, String payloadJson, String qaJson, String status,
                           String createdAt, Long templateId, Integer templateVersion,
                           String editedAt) {
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

    /**
     * 用户模板（FR-62 / FR-63）。
     * kind=content（内容模板）/ clip（成片模板，M5 预留）。
     * status=draft（示例学习拆解出的草稿，待人工确认）/ active（已启用，可用于生成）。
     * sourceNote=示例来源备注（FR-63，不存示例原文）。
     */
    public record TemplateRow(long id, String kind, String name, String voice, String opening,
                              String structureJson, String closing, String tagStyle, String tabooJson,
                              boolean builtin, Long originId, int version,
                              String status, String sourceNote,
                              String createdAt, String updatedAt) {
    }

    /** 资产（M7-1）：二进制素材一条记录；rel_path 相对 media 根（两种布局并存）。 */
    public record AssetRow(long id, String kind, String source, String name, String relPath,
                           String mime, long sizeBytes, int width, int height, double durationSec,
                           String sha256, String tagsJson, String createdAt) {
    }

    /** 资产关联（M7-1）：owner_kind=material|draft，owner_id 为对应表主键。 */
    public record AssetLinkRow(long id, long assetId, String ownerKind, long ownerId, String createdAt) {
    }

    /**
     * 一条异步任务（M8 第一片）。
     *
     * <p>`status` 只有五个取值：{@link #JOB_QUEUED} / {@link #JOB_RUNNING} /
     * {@link #JOB_SUCCEEDED} / {@link #JOB_FAILED} / {@link #JOB_CANCELED} —— 不设"隐含态"。
     */
    public record JobRow(long id, String kind, String status, String paramsJson,
                         Long inputAssetId, String inputPath, String outputPath,
                         String srtPath, String cutsPath, String workdir, String eventsPath,
                         String error, String resultJson,
                         String createdAt, String startedAt, String finishedAt) {
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
                        + "brief_json TEXT, payload_json TEXT, qa_json TEXT, status TEXT, created_at TEXT,"
                        + "template_id INTEGER, template_version INTEGER)",
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
                        + "insight TEXT NOT NULL, created_at TEXT)",
                // FR-62 用户模板（内容模板；clip 为成片模板预留）
                // FR-63 新增 status（draft|active）与 source_note（示例学习来源备注）
                "CREATE TABLE IF NOT EXISTS template ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "kind TEXT NOT NULL DEFAULT 'content',"
                        + "name TEXT NOT NULL, voice TEXT, opening TEXT,"
                        + "structure_json TEXT, closing TEXT, tag_style TEXT, taboo_json TEXT,"
                        + "builtin INTEGER NOT NULL DEFAULT 0, origin_id INTEGER,"
                        + "version INTEGER NOT NULL DEFAULT 1,"
                        + "status TEXT NOT NULL DEFAULT 'active', source_note TEXT,"
                        + "created_at TEXT, updated_at TEXT)",
                // M7-1 统一资产库（ADR-021）：二进制资产（图片/音频/视频/时间线）。
                // 与 material（文本素材）刻意分表，不迁移 —— 见 ADR-021 决策 1。
                // duration_sec 本里程碑恒为 0（ffprobe 在 Python 侧，接线属后续任务）。
                "CREATE TABLE IF NOT EXISTS asset ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "kind TEXT NOT NULL, source TEXT NOT NULL, name TEXT,"
                        + "rel_path TEXT NOT NULL, mime TEXT,"
                        + "size_bytes INTEGER DEFAULT 0, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0,"
                        + "duration_sec REAL DEFAULT 0, sha256 TEXT,"
                        + "tags_json TEXT DEFAULT '[]', created_at TEXT)",
                // 关联表：空 links 即"独立资产"，**不**为独立资产插 standalone 行（§2）
                "CREATE TABLE IF NOT EXISTS asset_link ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "asset_id INTEGER NOT NULL, owner_kind TEXT NOT NULL, owner_id INTEGER NOT NULL,"
                        + "created_at TEXT)",
                // 两条索引各自独立一条（sqlite-jdbc 单次 execute 只跑首条语句）
                "CREATE INDEX IF NOT EXISTS idx_asset_link_asset ON asset_link(asset_id)",
                "CREATE INDEX IF NOT EXISTS idx_asset_kind ON asset(kind)",
                // ===== M8 第一片：异步任务（粗剪）=====
                // 为什么落库而不是放内存：**重启后必须还看得见上次跑到哪、成了还是败了** ——
                // 分钟级任务里"重启一下进度就没了"是不可接受的（见 docs/spec_roughcut_jobs.md §4）。
                "CREATE TABLE IF NOT EXISTS job ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "kind TEXT NOT NULL, status TEXT NOT NULL,"
                        + "params_json TEXT, input_asset_id INTEGER, input_path TEXT,"
                        + "output_path TEXT, srt_path TEXT, cuts_path TEXT, workdir TEXT,"
                        + "events_path TEXT, error TEXT, result_json TEXT,"
                        + "created_at TEXT, started_at TEXT, finished_at TEXT)"
        };
        try (Connection c = DriverManager.getConnection(jdbcUrl); Statement st = c.createStatement()) {
            for (String sql : statements) {
                st.execute(sql);
            }
            ensureDraftTemplateColumns(c);
            ensureTemplateStatusColumns(c);
            ensureFactsAndEditColumns(c);
            ensureJobResultColumn(c);
        }
        log.info("SQLite schema ready");
    }

    /**
     * 老库补列：`job.result_json`（M8 第二片）。
     *
     * <p>为什么要单独一列：生成任务需要回显**每个平台**的结果（成了几篇、哪几个失败、为什么），
     * 而 `params_json` 是**输入**、`error` 只是一句话 —— 塞进去都会让"输入"和"输出"混在一起。
     */
    private void ensureJobResultColumn(Connection c) throws java.sql.SQLException {
        java.util.Set<String> cols = new java.util.HashSet<>();
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery("PRAGMA table_info(job)")) {
            while (rs.next()) {
                cols.add(rs.getString("name"));
            }
        }
        if (!cols.contains("result_json")) {
            try (Statement st = c.createStatement()) {
                st.execute("ALTER TABLE job ADD COLUMN result_json TEXT");
                log.info("migrated: job.result_json added");
            }
        }
    }

    /**
     * 老库补列：template.status / template.source_note（FR-63）。
     * 存量模板一律视为 active，保持既有行为不变。
     */
    private void ensureTemplateStatusColumns(Connection c) throws java.sql.SQLException {
        java.util.Set<String> cols = new java.util.HashSet<>();
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery("PRAGMA table_info(template)")) {
            while (rs.next()) {
                cols.add(rs.getString("name"));
            }
        }
        if (!cols.contains("status")) {
            try (Statement st = c.createStatement()) {
                st.execute("ALTER TABLE template ADD COLUMN status TEXT NOT NULL DEFAULT 'active'");
                log.info("migrated: template.status added");
            }
        }
        if (!cols.contains("source_note")) {
            try (Statement st = c.createStatement()) {
                st.execute("ALTER TABLE template ADD COLUMN source_note TEXT");
                log.info("migrated: template.source_note added");
            }
        }
    }

    /**
     * 老库补列：draft.template_id / draft.template_version。
     * SQLite 的 ADD COLUMN 不幂等（重复执行报 duplicate column），故先查 PRAGMA 再决定。
     */
    private void ensureDraftTemplateColumns(Connection c) throws java.sql.SQLException {
        java.util.Set<String> cols = new java.util.HashSet<>();
        try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery("PRAGMA table_info(draft)")) {
            while (rs.next()) {
                cols.add(rs.getString("name"));
            }
        }
        if (!cols.contains("template_id")) {
            try (Statement st = c.createStatement()) {
                st.execute("ALTER TABLE draft ADD COLUMN template_id INTEGER");
                log.info("migrated: draft.template_id added");
            }
        }
        if (!cols.contains("template_version")) {
            try (Statement st = c.createStatement()) {
                st.execute("ALTER TABLE draft ADD COLUMN template_version INTEGER");
                log.info("migrated: draft.template_version added");
            }
        }
    }

    // ---------------- 迁移（事实确认与稿件编辑闭环）----------------
    /** 增量加列：material.facts_confirmed / facts_confirmed_at、draft.edited_at。幂等。 */
    private void ensureFactsAndEditColumns(Connection c) throws java.sql.SQLException {
        addColumnIfMissing(c, "material", "facts_confirmed", "INTEGER DEFAULT 0");
        addColumnIfMissing(c, "material", "facts_confirmed_at", "TEXT");
        addColumnIfMissing(c, "draft", "edited_at", "TEXT");
    }

    /** 幂等加列助手：集中 PRAGMA 检查，避免重复样板代码。 */
    private void addColumnIfMissing(Connection c, String table, String column, String ddl)
            throws java.sql.SQLException {
        java.util.Set<String> cols = new java.util.HashSet<>();
        try (Statement st = c.createStatement();
             ResultSet rs = st.executeQuery("PRAGMA table_info(" + table + ")")) {
            while (rs.next()) {
                cols.add(rs.getString("name"));
            }
        }
        if (!cols.contains(column)) {
            try (Statement st = c.createStatement()) {
                st.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + ddl);
                log.info("migrated: {}.{} added", table, column);
            }
        }
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
        String sql = "SELECT id, raw_text, source_kind, title, core_message, tone, audience, facts_json, created_at, facts_confirmed, facts_confirmed_at "
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
        String sql = "SELECT id, raw_text, source_kind, title, core_message, tone, audience, facts_json, created_at, facts_confirmed, facts_confirmed_at "
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
                rs.getString("audience"), rs.getString("facts_json"), rs.getString("created_at"),
                rs.getInt("facts_confirmed") == 1, rs.getString("facts_confirmed_at"));
    }

    /** 保存（并可选确认）事实清单。
     *
     * <p>确认后该版本即成为后续生成的**唯一事实依据**（不再重复理解），
     * 见 `docs/40-事实确认与稿件编辑闭环-spec.md`。
     */
    public int saveFacts(long id, String factsJson, String coreMessage, String tone,
                         String audience, boolean confirm) {
        String sql = "UPDATE material SET facts_json=?, core_message=COALESCE(?, core_message),"
                + " tone=COALESCE(?, tone), audience=COALESCE(?, audience),"
                + " facts_confirmed=?, facts_confirmed_at=? WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, factsJson);
            ps.setString(2, coreMessage);
            ps.setString(3, tone);
            ps.setString(4, audience);
            ps.setInt(5, confirm ? 1 : 0);
            // 确认时记时间；未确认（仅暂存）则清空标记
            ps.setString(6, confirm ? LocalDateTime.now().toString() : null);
            ps.setLong(7, id);
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("saveFacts failed", e);
        }
    }

    // ---------------- draft ----------------
    /** 兼容旧调用：不记录模板来源。 */
    public long insertDraft(long materialId, String platformCode, String platformName,
                            String briefJson, String payloadJson, String qaJson, String status) {
        return insertDraft(materialId, platformCode, platformName, briefJson, payloadJson, qaJson,
                status, null, null);
    }

    /** FR-64：同时记录所用模板 id 与版本号（可追溯）。 */
    public long insertDraft(long materialId, String platformCode, String platformName,
                            String briefJson, String payloadJson, String qaJson, String status,
                            Long templateId, Integer templateVersion) {
        String sql = "INSERT INTO draft(material_id, platform_code, platform_name, brief_json, payload_json, qa_json, status, created_at, template_id, template_version) "
                + "VALUES(?,?,?,?,?,?,?,?,?,?)";
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
            if (templateId == null) {
                ps.setNull(9, java.sql.Types.INTEGER);
            } else {
                ps.setLong(9, templateId);
            }
            if (templateVersion == null) {
                ps.setNull(10, java.sql.Types.INTEGER);
            } else {
                ps.setInt(10, templateVersion);
            }
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertDraft failed", e);
        }
    }

    /** 保存人工编辑后的稿件正文（FR-42）。
     *
     * <p>只覆盖 payload_json 与 edited_at；brief/qa/clip_sheet 等**不经此路径**，
     * 避免绕过质检（见 spec §2.2）。
     */
    public int updateDraftPayload(long id, String payloadJson) {
        String sql = "UPDATE draft SET payload_json=?, edited_at=? WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, payloadJson);
            ps.setString(2, LocalDateTime.now().toString());
            ps.setLong(3, id);
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("updateDraftPayload failed", e);
        }
    }

    private static final String DRAFT_COLS =
            "id, material_id, platform_code, platform_name, brief_json, payload_json, qa_json, status, created_at, template_id, template_version, edited_at";

    private DraftRow mapDraft(ResultSet rs) throws java.sql.SQLException {
        long tid = rs.getLong("template_id");
        Long templateId = rs.wasNull() ? null : tid;
        int tv = rs.getInt("template_version");
        Integer templateVersion = rs.wasNull() ? null : tv;
        return new DraftRow(rs.getLong("id"), rs.getLong("material_id"),
                rs.getString("platform_code"), rs.getString("platform_name"),
                rs.getString("brief_json"), rs.getString("payload_json"),
                rs.getString("qa_json"), rs.getString("status"), rs.getString("created_at"),
                templateId, templateVersion, rs.getString("edited_at"));
    }

    public List<DraftRow> draftsByMaterial(long materialId) {
        String sql = "SELECT " + DRAFT_COLS + " FROM draft WHERE material_id=? ORDER BY id";
        List<DraftRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, materialId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(mapDraft(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("draftsByMaterial failed", e);
        }
        return out;
    }

    public Optional<DraftRow> getDraft(long id) {
        String sql = "SELECT " + DRAFT_COLS + " FROM draft WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapDraft(rs));
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

    // ---------------- template (FR-62) ----------------
    private static final String TEMPLATE_COLS =
            "id, kind, name, voice, opening, structure_json, closing, tag_style, taboo_json, "
                    + "builtin, origin_id, version, status, source_note, created_at, updated_at";

    private TemplateRow mapTemplate(ResultSet rs) throws java.sql.SQLException {
        long oid = rs.getLong("origin_id");
        Long originId = rs.wasNull() ? null : oid;
        String status = rs.getString("status");
        return new TemplateRow(
                rs.getLong("id"), rs.getString("kind"), rs.getString("name"),
                rs.getString("voice"), rs.getString("opening"), rs.getString("structure_json"),
                rs.getString("closing"), rs.getString("tag_style"), rs.getString("taboo_json"),
                rs.getInt("builtin") == 1, originId, rs.getInt("version"),
                status == null || status.isBlank() ? "active" : status,
                rs.getString("source_note"),
                rs.getString("created_at"), rs.getString("updated_at"));
    }

    /** 兼容旧调用：默认 status=active、无来源备注。 */
    public long insertTemplate(String kind, String name, String voice, String opening,
                               String structureJson, String closing, String tagStyle, String tabooJson,
                               boolean builtin, Long originId, int version) {
        return insertTemplate(kind, name, voice, opening, structureJson, closing, tagStyle, tabooJson,
                builtin, originId, version, "active", null);
    }

    /** FR-63：可指定 status（draft|active）与 source_note。 */
    public long insertTemplate(String kind, String name, String voice, String opening,
                               String structureJson, String closing, String tagStyle, String tabooJson,
                               boolean builtin, Long originId, int version,
                               String status, String sourceNote) {
        String sql = "INSERT INTO template(kind, name, voice, opening, structure_json, closing, tag_style, "
                + "taboo_json, builtin, origin_id, version, status, source_note, created_at, updated_at) "
                + "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)";
        String now = LocalDateTime.now().toString();
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, kind == null || kind.isBlank() ? "content" : kind);
            ps.setString(2, name);
            ps.setString(3, voice);
            ps.setString(4, opening);
            ps.setString(5, structureJson);
            ps.setString(6, closing);
            ps.setString(7, tagStyle);
            ps.setString(8, tabooJson);
            ps.setInt(9, builtin ? 1 : 0);
            if (originId == null) {
                ps.setNull(10, java.sql.Types.INTEGER);
            } else {
                ps.setLong(10, originId);
            }
            ps.setInt(11, Math.max(1, version));
            ps.setString(12, status == null || status.isBlank() ? "active" : status);
            ps.setString(13, sourceNote);
            ps.setString(14, now);
            ps.setString(15, now);
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertTemplate failed", e);
        }
    }

    public Optional<TemplateRow> getTemplate(long id) {
        String sql = "SELECT " + TEMPLATE_COLS + " FROM template WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapTemplate(rs));
                }
            }
            return Optional.empty();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("getTemplate failed", e);
        }
    }

    /** 列表：不过滤状态（返回全部）。 */
    public List<TemplateRow> listTemplates(String kind) {
        return listTemplates(kind, null);
    }

    /**
     * 列表查询：内置在前，我的在后；再按 id 升序。
     *
     * @param kind   类型过滤，null/空 表示不按 kind 过滤
     * @param status 状态过滤：{@code active} / {@code draft}；null 表示**不过滤**（返回全部）
     */
    public List<TemplateRow> listTemplates(String kind, String status) {
        List<String> conds = new ArrayList<>();
        List<String> args = new ArrayList<>();
        if (kind != null && !kind.isBlank()) {
            conds.add("kind=?");
            args.add(kind);
        }
        if (status != null && !status.isBlank()) {
            conds.add("status=?");
            args.add(status);
        }
        String sql = "SELECT " + TEMPLATE_COLS + " FROM template "
                + (conds.isEmpty() ? "" : "WHERE " + String.join(" AND ", conds) + " ")
                + "ORDER BY builtin DESC, id ASC";
        List<TemplateRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            for (int i = 0; i < args.size(); i++) {
                ps.setString(i + 1, args.get(i));
            }
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(mapTemplate(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("listTemplates failed", e);
        }
        return out;
    }

    /** FR-63：草稿 → 启用（仅 draft 会被更新，用于幂等判定）。 */
    public boolean activateTemplate(long id) {
        String sql = "UPDATE template SET status='active', updated_at=? WHERE id=? AND status<>'active'";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, LocalDateTime.now().toString());
            ps.setLong(2, id);
            return ps.executeUpdate() > 0;
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("activateTemplate failed", e);
        }
    }

    /** 按名称精确查找（导入冲突检测用）；重名时取 id 最小的一条。 */
    public Optional<TemplateRow> findTemplateByName(String name) {
        String sql = "SELECT " + TEMPLATE_COLS + " FROM template WHERE name=? ORDER BY id ASC LIMIT 1";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, name);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapTemplate(rs));
                }
            }
            return Optional.empty();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("findTemplateByName failed", e);
        }
    }

    /** 编辑模板：version 自增，updated_at 刷新。返回是否命中。 */
    public boolean updateTemplate(long id, String name, String voice, String opening,
                                  String structureJson, String closing, String tagStyle, String tabooJson) {
        String sql = "UPDATE template SET name=?, voice=?, opening=?, structure_json=?, closing=?, "
                + "tag_style=?, taboo_json=?, version=version+1, updated_at=? WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, name);
            ps.setString(2, voice);
            ps.setString(3, opening);
            ps.setString(4, structureJson);
            ps.setString(5, closing);
            ps.setString(6, tagStyle);
            ps.setString(7, tabooJson);
            ps.setString(8, LocalDateTime.now().toString());
            ps.setLong(9, id);
            return ps.executeUpdate() > 0;
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("updateTemplate failed", e);
        }
    }

    /** 覆盖同名模板内容（导入 on_conflict=overwrite）：不改 builtin，version 自增。 */
    public boolean overwriteTemplateByName(String name, String voice, String opening,
                                           String structureJson, String closing, String tagStyle,
                                           String tabooJson) {
        String sql = "UPDATE template SET voice=?, opening=?, structure_json=?, closing=?, "
                + "tag_style=?, taboo_json=?, version=version+1, updated_at=? WHERE name=? AND builtin=0";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, voice);
            ps.setString(2, opening);
            ps.setString(3, structureJson);
            ps.setString(4, closing);
            ps.setString(5, tagStyle);
            ps.setString(6, tabooJson);
            ps.setString(7, LocalDateTime.now().toString());
            ps.setString(8, name);
            return ps.executeUpdate() > 0;
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("overwriteTemplateByName failed", e);
        }
    }

    /** 删除模板（调用方负责拒绝 builtin）。 */
    public boolean deleteTemplate(long id) {
        String sql = "DELETE FROM template WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            return ps.executeUpdate() > 0;
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("deleteTemplate failed", e);
        }
    }

    public int countBuiltinTemplates() {
        String sql = "SELECT COUNT(*) FROM template WHERE builtin=1";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql);
             ResultSet rs = ps.executeQuery()) {
            return rs.next() ? rs.getInt(1) : 0;
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("countBuiltinTemplates failed", e);
        }
    }

    // ---------------- asset / asset_link（M7-1 统一资产库）----------------

    private static final String ASSET_COLS =
            "id, kind, source, name, rel_path, mime, size_bytes, width, height, duration_sec, "
                    + "sha256, tags_json, created_at";

    private AssetRow mapAsset(ResultSet rs) throws java.sql.SQLException {
        return new AssetRow(
                rs.getLong("id"), rs.getString("kind"), rs.getString("source"), rs.getString("name"),
                rs.getString("rel_path"), rs.getString("mime"),
                rs.getLong("size_bytes"), rs.getInt("width"), rs.getInt("height"),
                rs.getDouble("duration_sec"), rs.getString("sha256"),
                rs.getString("tags_json"), rs.getString("created_at"));
    }

    /** 插入一条资产记录，返回主键。去重靠 sha256 判断，不在这一层做（§3 允许同 sha 多条记录）。 */
    public long insertAsset(String kind, String source, String name, String relPath, String mime,
                            long sizeBytes, int width, int height, double durationSec,
                            String sha256, String tagsJson) {
        String sql = "INSERT INTO asset(kind, source, name, rel_path, mime, size_bytes, width, height, "
                + "duration_sec, sha256, tags_json, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, kind);
            ps.setString(2, source);
            ps.setString(3, name);
            ps.setString(4, relPath);
            ps.setString(5, mime);
            ps.setLong(6, Math.max(0L, sizeBytes));
            ps.setInt(7, Math.max(0, width));
            ps.setInt(8, Math.max(0, height));
            ps.setDouble(9, Math.max(0d, durationSec));
            ps.setString(10, sha256);
            ps.setString(11, tagsJson == null || tagsJson.isBlank() ? "[]" : tagsJson);
            ps.setString(12, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertAsset failed", e);
        }
    }

    public Optional<AssetRow> getAsset(long id) {
        String sql = "SELECT " + ASSET_COLS + " FROM asset WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(mapAsset(rs));
                }
            }
            return Optional.empty();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("getAsset failed", e);
        }
    }

    /**
     * 资产列表：kind 多值（空=不限）、q 按 name 模糊匹配；最新在前。
     * 与 {@link #countAssets} 共用 where 构造，避免两处条件漂移。
     */
    public List<AssetRow> listAssets(List<String> kinds, String q, int limit, int offset) {
        StringBuilder sql = new StringBuilder("SELECT " + ASSET_COLS + " FROM asset");
        List<String> args = appendAssetWhere(sql, kinds, q);
        sql.append(" ORDER BY id DESC LIMIT ? OFFSET ?");
        List<AssetRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql.toString())) {
            int i = bindAssetWhere(ps, args);
            ps.setInt(i++, Math.max(1, limit));
            ps.setInt(i, Math.max(0, offset));
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(mapAsset(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("listAssets failed", e);
        }
        return out;
    }

    /** 与 {@link #listAssets} 同一筛选条件的总数（前端分页显示用）。 */
    public int countAssets(List<String> kinds, String q) {
        StringBuilder sql = new StringBuilder("SELECT COUNT(*) FROM asset");
        List<String> args = appendAssetWhere(sql, kinds, q);
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql.toString())) {
            bindAssetWhere(ps, args);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getInt(1) : 0;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("countAssets failed", e);
        }
    }

    /** 追加 where 子句并返回占位符参数（按顺序）。 */
    private static List<String> appendAssetWhere(StringBuilder sql, List<String> kinds, String q) {
        List<String> conds = new ArrayList<>();
        List<String> args = new ArrayList<>();
        if (kinds != null && !kinds.isEmpty()) {
            conds.add("kind IN (" + "?,".repeat(kinds.size() - 1) + "?)");
            args.addAll(kinds);
        }
        if (q != null && !q.isBlank()) {
            conds.add("name LIKE ?");
            args.add("%" + q.trim() + "%");
        }
        if (!conds.isEmpty()) {
            sql.append(" WHERE ").append(String.join(" AND ", conds));
        }
        return args;
    }

    private static int bindAssetWhere(PreparedStatement ps, List<String> args) throws java.sql.SQLException {
        int i = 1;
        for (String a : args) {
            ps.setString(i++, a);
        }
        return i;
    }

    /** 删除资产记录（**不动** asset_link 与物理文件；删除语义编排在 AssetService）。 */
    public boolean deleteAsset(long id) {
        String sql = "DELETE FROM asset WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            return ps.executeUpdate() > 0;
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("deleteAsset failed", e);
        }
    }

    /** 同一 sha 的资产记录（删除时判断"是否还有别的记录引用这份文件"）。 */
    public List<AssetRow> assetsBySha(String sha256) {
        String sql = "SELECT " + ASSET_COLS + " FROM asset WHERE sha256=?";
        List<AssetRow> out = new ArrayList<>();
        if (sha256 == null || sha256.isBlank()) {
            return out;
        }
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, sha256);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(mapAsset(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("assetsBySha failed", e);
        }
        return out;
    }

    /** 建立资产↔宿主关联，返回主键（幂等由调用方先查 {@link #hasAssetLink} 保证）。 */
    public long insertAssetLink(long assetId, String ownerKind, long ownerId) {
        String sql = "INSERT INTO asset_link(asset_id, owner_kind, owner_id, created_at) VALUES(?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setLong(1, assetId);
            ps.setString(2, ownerKind);
            ps.setLong(3, ownerId);
            ps.setString(4, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertAssetLink failed", e);
        }
    }

    /** 解链：返回删除行数（删除资产的第 1 步）。 */
    public int deleteAssetLinksByAsset(long assetId) {
        String sql = "DELETE FROM asset_link WHERE asset_id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, assetId);
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("deleteAssetLinksByAsset failed", e);
        }
    }

    /** 某资产的全部关联（AssetDTO.links）。 */
    public List<AssetLinkRow> linksOf(long assetId) {
        String sql = "SELECT id, asset_id, owner_kind, owner_id, created_at FROM asset_link "
                + "WHERE asset_id=? ORDER BY id";
        List<AssetLinkRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, assetId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(new AssetLinkRow(rs.getLong("id"), rs.getLong("asset_id"),
                            rs.getString("owner_kind"), rs.getLong("owner_id"),
                            rs.getString("created_at")));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("linksOf failed", e);
        }
        return out;
    }

    // ================= M8 第一片：异步任务（粗剪）=================

    public static final String JOB_QUEUED = "queued";
    public static final String JOB_RUNNING = "running";
    public static final String JOB_SUCCEEDED = "succeeded";
    public static final String JOB_FAILED = "failed";
    public static final String JOB_CANCELED = "canceled";
    /**
     * **部分失败**：多平台任务里有的成了、有的没成。
     *
     * <p>为什么需要它：只有 succeeded/failed 的话，"3 个平台成 2 个"只能二选一 ——
     * 记 succeeded 会把失败藏起来，记 failed 又抹掉"已经产出了 2 篇"这个事实。
     * 工作台要能一眼看出"要去看一眼失败的哪个"（见 docs/checklist_async_generate.md §3）。
     */
    public static final String JOB_PARTIAL = "partial";

    /** 建任务（初始 `queued`）。 */
    public long insertJob(String kind, String paramsJson, Long inputAssetId, String inputPath,
                          String workdir, String eventsPath) {
        String sql = "INSERT INTO job(kind, status, params_json, input_asset_id, input_path, "
                + "workdir, events_path, created_at) VALUES(?,?,?,?,?,?,?,?)";
        try (Connection c = open();
             PreparedStatement ps = c.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, kind);
            ps.setString(2, JOB_QUEUED);
            ps.setString(3, paramsJson);
            if (inputAssetId == null) {
                ps.setNull(4, java.sql.Types.INTEGER);
            } else {
                ps.setLong(4, inputAssetId);
            }
            ps.setString(5, inputPath);
            ps.setString(6, workdir);
            ps.setString(7, eventsPath);
            ps.setString(8, LocalDateTime.now().toString());
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                return rs.next() ? rs.getLong(1) : -1L;
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("insertJob failed", e);
        }
    }

    /** 标记开始运行。 */
    public int markJobRunning(long id) {
        return updateJob("UPDATE job SET status=?, started_at=? WHERE id=?",
                JOB_RUNNING, LocalDateTime.now().toString(), id);
    }

    /** 落终态 + 每平台结果（生成任务用）。 */
    public int finishJobWithResult(long id, String status, String error, String resultJson) {
        String sql = "UPDATE job SET status=?, error=?, result_json=?, finished_at=? WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, status);
            ps.setString(2, error);
            ps.setString(3, resultJson);
            ps.setString(4, LocalDateTime.now().toString());
            ps.setLong(5, id);
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("finishJobWithResult failed", e);
        }
    }

    /** 落终态。`error` 只有失败时才有值。 */
    public int finishJob(long id, String status, String error, String outputPath,
                         String srtPath, String cutsPath) {
        String sql = "UPDATE job SET status=?, error=?, output_path=?, srt_path=?, cuts_path=?, "
                + "finished_at=? WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, status);
            ps.setString(2, error);
            ps.setString(3, outputPath);
            ps.setString(4, srtPath);
            ps.setString(5, cutsPath);
            ps.setString(6, LocalDateTime.now().toString());
            ps.setLong(7, id);
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("finishJob failed", e);
        }
    }

    private int updateJob(String sql, Object... args) {
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            for (int i = 0; i < args.length; i++) {
                ps.setObject(i + 1, args[i]);
            }
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("updateJob failed", e);
        }
    }

    public Optional<JobRow> getJob(long id) {
        String sql = "SELECT * FROM job WHERE id=?";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? Optional.of(readJob(rs)) : Optional.empty();
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("getJob failed", e);
        }
    }

    /** 最近的任务（新的在前）。 */
    public List<JobRow> listJobs(int limit) {
        String sql = "SELECT * FROM job ORDER BY id DESC LIMIT ?";
        List<JobRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setInt(1, Math.max(1, limit));
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(readJob(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("listJobs failed", e);
        }
        return out;
    }

    /** 找出所有"上次没跑完"的任务（重启后要把它们标成失败 —— 见下）。 */
    public List<JobRow> jobsInFlight() {
        String sql = "SELECT * FROM job WHERE status IN (?,?) ORDER BY id";
        List<JobRow> out = new ArrayList<>();
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setString(1, JOB_QUEUED);
            ps.setString(2, JOB_RUNNING);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    out.add(readJob(rs));
                }
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("jobsInFlight failed", e);
        }
        return out;
    }

    /** 只保留最近 keep 条，返回裁掉的行数（任务列表不该无限涨）。 */
    public int pruneJobs(int keep) {
        String sql = "DELETE FROM job WHERE id NOT IN (SELECT id FROM job ORDER BY id DESC LIMIT ?)";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setInt(1, Math.max(1, keep));
            return ps.executeUpdate();
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("pruneJobs failed", e);
        }
    }

    private static JobRow readJob(ResultSet rs) throws java.sql.SQLException {
        long assetId = rs.getLong("input_asset_id");
        Long boxed = rs.wasNull() ? null : assetId;
        return new JobRow(rs.getLong("id"), rs.getString("kind"), rs.getString("status"),
                rs.getString("params_json"), boxed, rs.getString("input_path"),
                rs.getString("output_path"), rs.getString("srt_path"), rs.getString("cuts_path"),
                rs.getString("workdir"), rs.getString("events_path"), rs.getString("error"),
                rs.getString("result_json"),
                rs.getString("created_at"), rs.getString("started_at"), rs.getString("finished_at"));
    }

    public boolean hasAssetLink(long assetId, String ownerKind, long ownerId) {
        String sql = "SELECT 1 FROM asset_link WHERE asset_id=? AND owner_kind=? AND owner_id=? LIMIT 1";
        try (Connection c = open(); PreparedStatement ps = c.prepareStatement(sql)) {
            ps.setLong(1, assetId);
            ps.setString(2, ownerKind);
            ps.setLong(3, ownerId);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        } catch (java.sql.SQLException e) {
            throw new RuntimeException("hasAssetLink failed", e);
        }
    }
}
