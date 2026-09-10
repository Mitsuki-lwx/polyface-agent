package com.polyface.backend.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.store.Store;

/**
 * 模板 ↔ JSON 映射（FR-62/FR-64）。
 * 三种视图：
 *  - toJson        管理界面用（含 id/builtin/version/origin_id）
 *  - toUserTemplate 透传 Python 用（仅 UserTemplate 字段，snake_case）
 *  - toExportJson  导出用（去掉本机标识：id/builtin/origin_id）
 */
public final class TemplateMapper {

    private static final ObjectMapper M = new ObjectMapper();

    private TemplateMapper() {
    }

    /** 管理视图。 */
    public static ObjectNode toJson(Store.TemplateRow t) {
        ObjectNode n = M.createObjectNode();
        n.put("id", t.id());
        n.put("kind", t.kind());
        n.put("name", t.name());
        n.put("voice", nullSafe(t.voice()));
        n.put("opening", nullSafe(t.opening()));
        n.set("structure", readArray(t.structureJson()));
        n.put("closing", nullSafe(t.closing()));
        n.put("tag_style", nullSafe(t.tagStyle()));
        n.set("taboo", readArray(t.tabooJson()));
        n.put("builtin", t.builtin());
        if (t.originId() == null) {
            n.putNull("origin_id");
        } else {
            n.put("origin_id", t.originId());
        }
        n.put("version", t.version());
        n.put("created_at", nullSafe(t.createdAt()));
        n.put("updated_at", nullSafe(t.updatedAt()));
        return n;
    }

    /** 透传 Python 的 UserTemplate 结构（与 schemas_gen.UserTemplate 字段对齐）。 */
    public static ObjectNode toUserTemplate(Store.TemplateRow t) {
        ObjectNode n = M.createObjectNode();
        n.put("name", t.name());
        n.put("voice", nullSafe(t.voice()));
        n.put("opening", nullSafe(t.opening()));
        n.set("structure", readArray(t.structureJson()));
        n.put("closing", nullSafe(t.closing()));
        n.put("tag_style", nullSafe(t.tagStyle()));
        n.set("taboo", readArray(t.tabooJson()));
        return n;
    }

    /** 导出视图（跨机器可用，不含本机 id）。 */
    public static ObjectNode toExportJson(Store.TemplateRow t) {
        ObjectNode n = M.createObjectNode();
        n.put("kind", t.kind());
        n.put("name", t.name());
        n.put("voice", nullSafe(t.voice()));
        n.put("opening", nullSafe(t.opening()));
        n.set("structure", readArray(t.structureJson()));
        n.put("closing", nullSafe(t.closing()));
        n.put("tag_style", nullSafe(t.tagStyle()));
        n.set("taboo", readArray(t.tabooJson()));
        return n;
    }

    private static String nullSafe(String s) {
        return s == null ? "" : s;
    }

    private static ArrayNode readArray(String json) {
        ArrayNode empty = M.createArrayNode();
        if (json == null || json.isBlank()) {
            return empty;
        }
        try {
            var node = M.readTree(json);
            return node.isArray() ? (ArrayNode) node : empty;
        } catch (Exception e) {
            return empty;
        }
    }
}
