package com.polyface.backend.web;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.client.PythonClient;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 时间线读写（`docs/spec_timeline.md`、`docs/tasks_timeline.md` T5）。
 *
 * <p>时间线是**数据**：保留哪些区间 + 字幕是什么。改它就能改剪辑结果 ——
 * 这就是 `docs/71` §2.2 说的"把『剪』变成『改数据』"。
 *
 * <p>**校验只有一份实现**（Python 的纯函数，通过 {@code /timeline/validate} 调用）。
 * Java 侧刻意**不**重复写区间校验 —— 两边各写一遍必然分叉，
 * 而分叉的后果是"界面说没问题、渲染却剪错"。
 */
@RestController
public class TimelineController {

    private static final Logger log = LoggerFactory.getLogger(TimelineController.class);

    private final Store store;
    private final AssetService assets;
    private final MediaDir mediaDir;
    private final PythonClient python;
    private final ObjectMapper mapper = new ObjectMapper();

    public TimelineController(Store store, AssetService assets, MediaDir mediaDir,
                              PythonClient python) {
        this.store = store;
        this.assets = assets;
        this.mediaDir = mediaDir;
        this.python = python;
    }

    /** 读一条时间线：内容 + 源视频信息（前端要拿源时长来判越界）。 */
    @GetMapping(value = "/api/timelines/{assetId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode get(@PathVariable long assetId) {
        Store.AssetRow row = assetRow(assetId);
        Path file = mediaDir.resolveSafe(row.relPath());
        if (!Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "时间线文件不在磁盘上：" + row.relPath());
        }
        if (!isTimelineFile(file)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "这个资产的内容不是时间线：" + row.relPath());
        }
        JsonNode timeline;
        try {
            timeline = mapper.readTree(Files.readString(file, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "时间线读不出来（可能被外部改坏了）：" + e.getMessage());
        }

        ObjectNode out = mapper.createObjectNode();
        out.put("asset_id", assetId);
        out.put("name", row.name());
        out.put("rel_path", row.relPath());
        // 用权威构造器（MediaController.mediaUrl）—— 自己拼前缀实测踩过：
        // 托管路径是 `/api/media/**`，写成 `/media/` 就是 404（预览帧全空）。
        out.put("url", MediaController.mediaUrl(row.relPath()));
        out.set("timeline", timeline);

        // 源视频：从血缘里取（粗剪登记时挂的就是它）
        ObjectNode src = out.putObject("source");
        src.put("duration", timeline.path("source").path("duration").asDouble(0));
        for (Store.AssetLinkRow l : store.linksOf(assetId)) {
            if ("asset".equals(l.ownerKind())) {
                store.getAsset(l.ownerId()).ifPresent(a -> {
                    src.put("asset_id", a.id());
                    src.put("name", a.name());
                    src.put("rel_path", a.relPath());
                    src.put("url", MediaController.mediaUrl(a.relPath()));
                });
            }
        }
        return out;
    }

    /** 只校验不存（前端编辑时用）。理由原样透传 —— 要能看出是哪一段不对。 */
    @PostMapping(value = "/api/timelines/validate", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode validate(@RequestBody JsonNode body) {
        return callValidate(body);
    }

    /**
     * 保存：**先校验，通过才落盘**。
     *
     * <p>顺序不能反 —— 先写再校验的话，校验不通过时磁盘上已经是一份坏数据了。
     * 落盘后刷新资产的 size/sha（资产是内容寻址的，文件改了摘要必须跟着改）。
     */
    @PutMapping(value = "/api/timelines/{assetId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode save(@PathVariable long assetId, @RequestBody JsonNode body) {
        Store.AssetRow row = assetRow(assetId);
        JsonNode checked = callValidate(body);                 // 不通过会抛 400
        JsonNode normalized = checked.path("normalized");

        Path file = mediaDir.resolveSafe(row.relPath());
        try {
            // 临时文件 + 改名：写一半崩掉不能留下一份半截时间线
            Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
            Files.writeString(tmp, mapper.writerWithDefaultPrettyPrinter()
                    .writeValueAsString(normalized), StandardCharsets.UTF_8);
            Files.move(tmp, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "时间线写入失败：" + e.getMessage());
        }
        assets.refreshFileMeta(assetId);
        log.info("timeline saved: asset={} keep={} segments={}", assetId,
                normalized.path("keep").size(), normalized.path("segments").size());

        ObjectNode out = mapper.createObjectNode();
        out.put("asset_id", assetId);
        out.put("saved", true);
        out.set("timeline", normalized);
        return out;
    }

    // ---------------- 内部 ----------------

    private Store.AssetRow assetRow(long assetId) {
        Store.AssetRow row = store.getAsset(assetId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        if (!"timeline".equals(row.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "这不是时间线资产（当前类型：" + row.kind() + "）");
        }
        return row;
    }

    /**
     * 这个文件**真的是**一条时间线吗？
     *
     * <p>`timeline` 在资产模型里是个**类**（json/srt/vtt/ass 都归它），所以只判 kind 不够 ——
     * 实测踩过：`/api/timelines` 把 `render.srt` 也列了出来，`GET` 它还把字幕文本当 JSON 解。
     * 判据只能是**内容**：有 `version` 且有 `keep` 才是时间线（粗剪的 `cuts.json` 只有 `cuts`，不是）。
     */
    private boolean isTimelineFile(Path f) {
        try {
            JsonNode n = mapper.readTree(Files.readString(f, StandardCharsets.UTF_8));
            return n.isObject() && n.has("version") && n.has("keep");
        } catch (Exception e) {                                // noqa: BLE001
            return false;
        }
    }

    /** 调 Python 校验。**Python 返回 400 时把它的理由原样翻成我们的错误体** —— 不能吞掉。 */
    private JsonNode callValidate(JsonNode body) {
        ObjectNode req = mapper.createObjectNode();
        req.set("timeline", body.has("timeline") ? body.path("timeline") : body);
        JsonNode dur = req.path("timeline").path("source").path("duration");
        if (dur.isNumber()) {
            req.put("source_duration", dur.asDouble());
        }
        try {
            return python.validateTimeline(req);
        } catch (Exception e) {                                // noqa: BLE001
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, extractDetail(e.getMessage()));
        }
    }

    /** 从 RestClient 的异常里抠出 Python 的 `detail`（它是给人看的理由，别丢）。 */
    private static String extractDetail(String msg) {
        if (msg == null) {
            return "时间线不合法";
        }
        int i = msg.indexOf("\"detail\"");
        if (i < 0) {
            return "时间线不合法：" + msg;
        }
        int colon = msg.indexOf(':', i);
        int end = msg.indexOf('"', colon + 3);
        return end > colon ? msg.substring(colon + 2, end) : msg;
    }

    /** 供前端列"可编辑的时间线"（比如素材库筛选用）。 */
    @GetMapping(value = "/api/timelines", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode list() {
        ObjectNode out = mapper.createObjectNode();
        ArrayNode items = out.putArray("items");
        for (Store.AssetRow a : store.listAssets(List.of("timeline"), null, 100, 0)) {
            // 同 kind 里还混着 srt/vtt —— 只列内容真是时间线的
            if (!isTimelineFile(mediaDir.resolveSafe(a.relPath()))) {
                continue;
            }
            ObjectNode n = items.addObject();
            n.put("asset_id", a.id());
            n.put("name", a.name());
            n.put("rel_path", a.relPath());
            n.put("url", MediaController.mediaUrl(a.relPath()));
            n.put("created_at", a.createdAt());
        }
        return out;
    }
}
