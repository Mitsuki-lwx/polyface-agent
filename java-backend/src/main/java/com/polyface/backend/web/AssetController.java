package com.polyface.backend.web;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.store.Store;

/**
 * 统一资产库（M7-1）：素材库面板的后端入口。
 *
 * <p>只做参数整形与状态码，落盘/探测/删除语义都在 {@link AssetService}；
 * 字段名逐字对齐 docs/73 §4（前端契约冻结）。
 */
@RestController
public class AssetController {

    /**
     * 允许的关联宿主。
     *
     * <p>`asset` 是 M8 第一片加的：粗剪产物要挂到**源视频**上（"这条成片是从哪个视频剪的"）。
     * `standalone` 不是一种可写入的 owner —— 它只是"没有任何 link"的语义。
     */
    private static final Set<String> OWNER_KINDS = Set.of("material", "draft", "asset");

    private static final int DEFAULT_LIMIT = 50;
    /** 上限 200：素材库是缩略图网格，一次给太多只会拖垮浏览器。 */
    private static final int MAX_LIMIT = 200;

    private final AssetService assets;
    private final ObjectMapper mapper = new ObjectMapper();

    public AssetController(AssetService assets) {
        this.assets = assets;
    }

    /** 关联请求体（docs/73 §4.3）。 */
    public record LinkBody(String owner_kind, Long owner_id) {
    }

    // ---------------- 列表 ----------------

    @GetMapping(value = "/api/assets", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode list(@RequestParam(value = "kind", required = false) String kind,
                           @RequestParam(value = "q", required = false) String q,
                           @RequestParam(value = "limit", defaultValue = "50") int limit,
                           @RequestParam(value = "offset", defaultValue = "0") int offset) {
        List<String> kinds = splitCsv(kind);
        int cappedLimit = Math.min(Math.max(1, limit), MAX_LIMIT);
        int safeOffset = Math.max(0, offset);

        ObjectNode out = mapper.createObjectNode();
        ArrayNode items = out.putArray("items");
        for (Store.AssetRow row : assets.list(kinds, q, cappedLimit, safeOffset)) {
            items.add(mapper.valueToTree(assets.toDto(row)));
        }
        out.put("total", assets.count(kinds, q));
        out.put("limit", cappedLimit);
        out.put("offset", safeOffset);
        return out;
    }

    @GetMapping(value = "/api/assets/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public AssetService.AssetDto get(@PathVariable long id) {
        return assets.get(id).map(assets::toDto)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
    }

    // ---------------- 上传 ----------------

    @PostMapping(value = "/api/assets/upload", produces = MediaType.APPLICATION_JSON_VALUE)
    public AssetService.AssetDto upload(@RequestParam("file") MultipartFile file,
                                        @RequestParam(value = "name", required = false) String name,
                                        @RequestParam(value = "tags", required = false) String tags)
            throws IOException {
        long id = assets.upload(file, name, tags);
        return assets.get(id).map(assets::toDto)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "资产登记失败"));
    }

    // ---------------- 关联 ----------------

    /** 幂等：重复 link 返回 200 且 links 不重复增长。 */
    @PostMapping(value = "/api/assets/{id}/link", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode link(@PathVariable long id, @RequestBody(required = false) LinkBody body) {
        if (body == null || body.owner_kind() == null || body.owner_kind().isBlank()
                || body.owner_id() == null || body.owner_id() <= 0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "owner_kind / owner_id 必填");
        }
        String ownerKind = body.owner_kind().trim();
        if (!OWNER_KINDS.contains(ownerKind)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "owner_kind 只支持 material | draft");
        }
        Store.AssetRow row = assets.get(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        assets.link(id, ownerKind, body.owner_id());

        ObjectNode out = mapper.createObjectNode();
        out.put("ok", true);
        out.set("links", mapper.valueToTree(assets.toDto(row).links()));
        return out;
    }

    // ---------------- 删除 ----------------

    @DeleteMapping(value = "/api/assets/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode delete(@PathVariable long id) {
        AssetService.DeleteResult res = assets.delete(id);
        ObjectNode out = mapper.createObjectNode();
        out.put("ok", true);
        out.put("file_removed", res.fileRemoved());
        out.put("links_removed", res.linksRemoved());
        return out;
    }

    // ---------------- helpers ----------------

    /** 逗号分隔多值 → 去空列表（kind=image,video）。 */
    private static List<String> splitCsv(String raw) {
        List<String> out = new ArrayList<>();
        if (raw == null) {
            return out;
        }
        for (String part : raw.split(",")) {
            String v = part.trim();
            if (!v.isEmpty()) {
                out.add(v);
            }
        }
        return out;
    }
}
