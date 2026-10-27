package com.polyface.backend.web;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.polyface.backend.asset.AssetService;
import com.polyface.backend.job.GenerateRunner;
import com.polyface.backend.job.JobProgress;
import com.polyface.backend.job.RoughcutRunner;
import com.polyface.backend.job.SceneSplitRunner;
import com.polyface.backend.job.SplitRunner;
import com.polyface.backend.media.MediaDir;
import com.polyface.backend.store.Store;

/**
 * 异步任务：粗剪（M8 第一片）与生成（第二片）的发起 / 查询 / 取消 / 重试。
 *
 * <p>分工与既有控制器一致：这里只做参数整形与状态码；
 * 执行在两个 Runner，状态在 {@link Store}，进度从**阶段事件文件**读。
 */
@RestController
public class JobController {

    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;

    public static final String KIND_ROUGHCUT = "roughcut";
    public static final String KIND_GENERATE = "generate";
    /** 从时间线渲染成片（`docs/spec_timeline.md`）—— 输入是**时间线资产**。 */
    public static final String KIND_RENDER = "render_timeline";
    /** 图文成片（`docs/spec_b1.md`）—— 输入是成稿的**剪辑单** + 用户选的图/BGM。 */
    public static final String KIND_MAKE_VIDEO = "make_video";
    /** 素材拆分：一个素材 → 多个素材（docs/spec_split.md）。 */
    public static final String KIND_SPLIT = "split";
    /** 按场景切段（docs/spec_scene_split.md T7）：两段式 —— 先方案（dry-run），再按勾选切。 */
    public static final String KIND_SCENE_SPLIT = "scene_split";

    private final Store store;
    private final RoughcutRunner roughcut;
    private final GenerateRunner generate;
    private final SplitRunner split;
    private final AssetService assets;
    private final MediaDir mediaDir;
    private final SceneSplitRunner sceneSplit;
    private final ObjectMapper mapper = new ObjectMapper();

    public JobController(Store store, RoughcutRunner roughcut, GenerateRunner generate,
                         AssetService assets, MediaDir mediaDir, SplitRunner split,
                         SceneSplitRunner sceneSplit) {
        this.store = store;
        this.roughcut = roughcut;
        this.generate = generate;
        this.assets = assets;
        this.mediaDir = mediaDir;
        this.split = split;
        this.sceneSplit = sceneSplit;
    }

    /** 发起请求体。粗剪用 `asset_id`；生成用 `material_id` + `platforms`。 */
    public record StartBody(String kind, Long asset_id, Long material_id, List<String> platforms,
                            Long template_id, JsonNode template, String tone_override,
                            Map<String, Object> params,
                            // 图文成片（docs/spec_b1.md）：每个分镜一张图 + 一首 BGM，都是素材库里的资产
                            List<Long> images, Long bgm_asset_id, String platform,
                            // 素材拆分（docs/spec_split.md）：角度 / 形态，至少要给一个
                            List<String> angles, List<String> formats,
                            // 拆分的角度多样性（不传则用 Python 侧默认 0.6）
                            Double temperature,
                            // 配音方案（docs/spec_tts_plugin.md）：插件 id + 音色 id
                            String plugin, String voice,
                            // 按场景切段（docs/spec_scene_split.md T7）：
                            // 方案阶段不填；切段阶段指向方案任务 + 勾选的段号
                            Long plan_job_id, List<Integer> pick) {
    }

    // ---------------- 发起 ----------------

    @PostMapping(value = "/api/jobs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode start(@RequestBody StartBody body) {
        if (body == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少请求体");
        }
        String kind = body.kind() == null || body.kind().isBlank() ? KIND_ROUGHCUT : body.kind();
        long jobId = switch (kind) {
            case KIND_ROUGHCUT -> startRoughcut(body);
            case KIND_GENERATE -> startGenerate(body);
            case KIND_RENDER -> startRender(body);
            case KIND_MAKE_VIDEO -> startMakeVideo(body);
            case KIND_SPLIT -> startSplit(body);
            case KIND_SCENE_SPLIT -> startSceneSplit(body);
            default -> throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "暂不支持的任务类型：" + kind);
        };
        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", jobId);
        out.put("kind", kind);
        out.put("status", Store.JOB_QUEUED);
        return out;
    }

    private long startRoughcut(StartBody body) {
        if (body.asset_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 asset_id");
        }
        Store.AssetRow row = assets.get(body.asset_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        if (!"video".equals(row.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "只有视频能粗剪，当前资产是 " + row.kind());
        }
        Path file = mediaDir.resolveSafe(row.relPath());
        if (!java.nio.file.Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "资产文件不在磁盘上：" + row.relPath());
        }
        Map<String, Object> params = body.params() == null ? Map.of() : body.params();
        return roughcut.submit(row.id(), file.toString(), params, "out");
    }

    private long startGenerate(StartBody body) {
        if (body.material_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 material_id");
        }
        store.getMaterial(body.material_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "素材不存在"));
        List<String> platforms = body.platforms() == null || body.platforms().isEmpty()
                ? List.of("xhs") : body.platforms();
        return generate.submit(body.material_id(), platforms, body.template_id(),
                body.template(), body.tone_override());
    }

    /**
     * 素材拆分（`docs/spec_split.md`）：一个素材 → 多个**素材**。
     *
     * <p>走任务而不是同步调用：一次 LLM 调用在长素材上要 1~3 分钟，
     * 同步时界面只能干等、进程被打断就什么都不知道了。
     *
     * <p>校验放在**提交时**（素材在不在、两维是不是都没给）—— 这类错误要当场说清，
     * 不该变成一条失败的任务让用户去任务列表里找。
     */
    private long startSplit(StartBody body) {
        if (body.material_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 material_id");
        }
        store.getMaterial(body.material_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "素材不存在"));
        List<String> angles = clean(body.angles());
        List<String> formats = clean(body.formats());
        if (angles.isEmpty() && formats.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "至少要给角度或形态之一（不替你决定拆几条 —— 见 spec 关键决定二）");
        }
        return split.submit(body.material_id(), angles, formats, body.temperature());
    }

    private static List<String> clean(List<String> in) {
        if (in == null) {
            return List.of();
        }
        List<String> out = new java.util.ArrayList<>();
        for (String s : in) {
            if (s != null && !s.isBlank()) {
                out.add(s.trim());
            }
        }
        return out;
    }

    /**
     * 按场景切段（`docs/spec_scene_split.md` T7）—— **两段式**。
     *
     * <p>方案阶段：只给 `asset_id` + `params` → 出方案（切点 + 缩略图），不切视频。
     * 切段阶段：再带 `plan_job_id` + `pick` → 切勾选的段并登记成素材。
     *
     * <p>`pick` 为空**当场拒绝**（400）：「没勾就全切」是隐式行为 —— 用户少勾了几个
     * 却多出几条素材，比报错更糟（同 spec 关键决定的人工确认语义）。
     */
    private long startSceneSplit(StartBody body) {
        if (body.asset_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 asset_id");
        }
        Store.AssetRow row = assets.get(body.asset_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        if (!"video".equals(row.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "只有视频能切场景，当前资产是 " + row.kind());
        }
        Path file = mediaDir.resolveSafe(row.relPath());
        if (!java.nio.file.Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "资产文件不在磁盘上：" + row.relPath());
        }
        if (body.plan_job_id() == null) {
            Map<String, Object> params = body.params() == null ? Map.of() : body.params();
            return sceneSplit.submitPlan(row.id(), file.toString(), params);
        }
        List<Integer> pick = body.pick() == null ? List.of() : body.pick();
        if (pick.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "没有勾选任何段（请至少勾选一段；不替你决定全切）");
        }
        try {
            return sceneSplit.submitCut(row.id(), file.toString(), body.plan_job_id(), pick);
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, e.getMessage());
        }
    }

    /**
     * 从时间线渲染成片：`asset_id` 指向**时间线资产**（不是源视频）。
     *
     * <p>产出新成片，**不覆盖**源视频与旧成片；血缘挂回时间线。
     * 它和粗剪一样是分钟级任务（要从源视频重剪 + 烧字幕），所以走任务队列。
     */
    private long startRender(StartBody body) {
        if (body.asset_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 asset_id（时间线资产）");
        }
        Store.AssetRow row = assets.get(body.asset_id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "资产不存在"));
        if (!"timeline".equals(row.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "只有时间线能渲染，当前资产是 " + row.kind());
        }
        Path file = mediaDir.resolveSafe(row.relPath());
        if (!java.nio.file.Files.isRegularFile(file)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND,
                    "时间线文件不在磁盘上：" + row.relPath());
        }
        Map<String, Object> params = body.params() == null ? Map.of() : body.params();
        return roughcut.submitRender(row.id(), file.toString(), params);
    }

    /**
     * 图文成片（`docs/spec_b1.md`）：从成稿的**剪辑单**起步。
     *
     * <p>计划由 Java 侧拼（Python 不读库）：分镜取剪辑单，图与 BGM 取用户选的资产。
     * 缺图的分镜要**当场说清是第几个** —— 不要等到合成跑了几分钟才报错。
     */
    private long startMakeVideo(StartBody body) {
        if (body.material_id() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 material_id");
        }
        String platform = body.platform() == null || body.platform().isBlank()
                ? (body.platforms() == null || body.platforms().isEmpty() ? null
                   : body.platforms().get(0))
                : body.platform();
        if (platform == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "缺少 platform（要按哪个平台的成稿做）");
        }
        List<Long> images = body.images() == null ? List.of() : body.images();

        // 成稿里带的剪辑单 —— 成片的分镜结构就来自它
        JsonNode sheet = null;
        for (Store.DraftRow d : store.draftsByMaterial(body.material_id())) {
            if (platform.equals(d.platformCode())) {
                sheet = readJson(d.payloadJson()).path("clip_sheet");
                break;
            }
        }
        if (sheet == null || sheet.isMissingNode() || sheet.path("scenes").isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "这个平台（" + platform + "）的成稿里没有剪辑单 —— 只有视频类平台的成稿才有分镜");
        }
        ArrayNode scenes = (ArrayNode) sheet.path("scenes");
        if (images.size() != scenes.size()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "图片数量与分镜数对不上：分镜 " + scenes.size() + " 个，给了 " + images.size() + " 张");
        }

        ArrayNode planScenes = mapper.createArrayNode();
        List<String> missing = new ArrayList<>();
        for (int i = 0; i < scenes.size(); i++) {
            JsonNode sc = scenes.get(i);
            ObjectNode one = mapper.createObjectNode();
            one.put("seq", i + 1);
            one.put("script", sc.path("script").asText(""));
            one.put("subtitle", sc.path("subtitle").asText(""));
            one.put("duration_hint", sc.path("duration_hint").asText(""));
            String img = assetPath(images.get(i));
            if (img == null) {
                missing.add(String.valueOf(i + 1));      // 指出是第几个
            }
            one.put("image", img == null ? "" : img);
            planScenes.add(one);
        }
        if (!missing.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "第 " + missing + " 个分镜没有可用的图片（图片必须是素材库里的图）");
        }

        ObjectNode plan = mapper.createObjectNode();
        plan.set("scenes", planScenes);
        if (body.bgm_asset_id() != null) {
            String bgm = assetPath(body.bgm_asset_id());
            if (bgm == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                        "BGM 资产不可用（必须是素材库里的音频）");
            }
            plan.put("bgm", bgm);
        }
        plan.putObject("source").put("material_id", body.material_id()).put("platform", platform);

        // 配音方案随 params 传下去 → 变成 make_video.py 的 --plugin / --voice
        Map<String, Object> params = new java.util.LinkedHashMap<>(
                body.params() == null ? Map.of() : body.params());
        if (body.plugin() != null && !body.plugin().isBlank()) {
            params.put("plugin", body.plugin());
        }
        if (body.voice() != null && !body.voice().isBlank()) {
            params.put("voice", body.voice());
        }
        return roughcut.submitMakeVideo(plan.toString(), params);
    }

    /** 资产 id → 磁盘路径。不是素材库里的资产（或文件不在）返回 null。 */
    private String assetPath(Long assetId) {
        if (assetId == null) {
            return null;
        }
        return assets.get(assetId)
                .map(a -> mediaDir.resolveSafe(a.relPath()).toString())
                .filter(p -> java.nio.file.Files.isRegularFile(java.nio.file.Path.of(p)))
                .orElse(null);
    }

    // ---------------- 重试失败平台 ----------------

    /**
     * 只重跑**失败的平台**，且**新建一个任务**。
     *
     * <p>为什么必须新建：原任务记录的是"上次跑了什么、结果如何" —— 就地改它，
     * 那段历史就查不到了（`docs/checklist_async_generate.md` §5）。
     */
    @PostMapping(value = "/api/jobs/{id}/retry", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode retry(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        if (!KIND_GENERATE.equals(j.kind())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "只有生成任务支持重试");
        }
        if (Store.JOB_INTERRUPTED.equals(j.status())) {
            // retry 会**从头**重跑；中断的任务该用 resume 接着跑。
            // 不拦的话，用户会以为"接着跑了"，其实把已花的额度又花了一遍。
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "这个任务是「已中断」—— 请用 resume 接着跑；retry 会从头重跑，"
                            + "把已经跑过的部分再花一遍额度");
        }
        List<String> failed = failedPlatforms(j);
        if (failed.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "这个任务没有失败的平台");
        }
        ObjectNode params = readJson(j.paramsJson());
        long materialId = params.path("material_id").asLong(0);
        Long templateId = params.has("template_id") ? params.path("template_id").asLong() : null;
        String tone = params.path("tone_override").asText(null);
        JsonNode inlineTpl = params.has("template") ? params.path("template") : null;
        long newId = generate.submit(materialId, failed, templateId, inlineTpl, tone);

        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", newId);
        out.put("retried_from", id);
        ArrayNode arr = out.putArray("platforms");
        failed.forEach(arr::add);
        return out;
    }

    /** 从任务结果里取出失败的平台列表。 */
    private List<String> failedPlatforms(Store.JobRow j) {
        List<String> out = new ArrayList<>();
        for (JsonNode n : readJson(j.resultJson()).path("failed")) {
            String code = n.path("platform_code").asText("");
            if (!code.isBlank()) {
                out.add(code);
            }
        }
        return out;
    }

    // ---------------- 继续（断点续跑）----------------

    /**
     * 继续一个「已中断」的任务：**跳过已完成的单位**，只补没跑完的。
     *
     * <p>与 {@link #retry(long)} 的区别必须明确，否则用户分不清：
     * <table border="1">
     *   <tr><th></th><th>retry</th><th>resume</th></tr>
     *   <tr><td>适用状态</td><td>{@code failed}</td><td>{@code interrupted}</td></tr>
     *   <tr><td>行为</td><td><b>从头</b>再跑</td><td><b>跳过已完成</b>，只补缺的</td></tr>
     * </table>
     *
     * <p>状态不对时**明确报错**，不静默接受 —— 静默接受会让用户以为"续跑了"，
     * 其实是"重跑了"（见 `docs/spec_resume.md` §5、`docs/tasks_resume.md` T6）。
     */
    @PostMapping(value = "/api/jobs/{id}/resume", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode resume(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        if (Store.JOB_FAILED.equals(j.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "这个任务是**失败**（不是中断）—— 没有可续的部分，请用 retry 从头重跑");
        }
        if (!Store.JOB_INTERRUPTED.equals(j.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "只有「已中断」的任务能继续，当前状态：" + j.status());
        }
        boolean accepted = switch (j.kind()) {
            case KIND_GENERATE -> generate.resume(id);
            case KIND_SPLIT -> split.resume(id);
            case KIND_SCENE_SPLIT -> sceneSplit.resume(id);
            default -> roughcut.resume(id);
        };
        if (!accepted) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "任务状态已变化（可能已被取消或已开始跑），请刷新后重试");
        }
        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", id);
        out.put("resumed", true);
        out.put("status", store.getJob(id).map(Store.JobRow::status).orElse(j.status()));
        return out;
    }

    // ---------------- 查询 ----------------

    @GetMapping(value = "/api/jobs", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode list(@RequestParam(value = "limit", defaultValue = "20") int limit,
                           @RequestParam(value = "kind", required = false) String kind) {
        ObjectNode out = mapper.createObjectNode();
        ArrayNode items = out.putArray("items");
        for (Store.JobRow j : store.listJobs(Math.min(Math.max(limit, 1), MAX_LIMIT))) {
            if (kind != null && !kind.isBlank() && !kind.equals(j.kind())) {
                continue;
            }
            items.add(toDto(j));
        }
        return out;
    }

    @GetMapping(value = "/api/jobs/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode get(@PathVariable long id) {
        return toDto(store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在")));
    }

    @PostMapping(value = "/api/jobs/{id}/cancel", produces = MediaType.APPLICATION_JSON_VALUE)
    public ObjectNode cancel(@PathVariable long id) {
        Store.JobRow j = store.getJob(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "任务不存在"));
        // 按种类分派（以前是个三元表达式：只有 generate 和"其它"两条路，
        // 加了 split 之后会静默掉进 roughcut —— 取消一个不存在的子进程，然后什么也不发生）
        boolean killed = switch (j.kind()) {
            case KIND_GENERATE -> generate.cancel(id);
            case KIND_SPLIT -> split.cancel(id);
            case KIND_SCENE_SPLIT -> sceneSplit.cancel(id);
            default -> roughcut.cancel(id);
        };
        // 已中断的任务**没有进程可杀**，但它确实需要能被"放弃" ——
        // 否则它会永远躺在"可继续"里，用户既跑不掉也清不掉（见 docs/tasks_resume.md T8）。
        //
        // **2026-10-27 补 queued/running**：`RoughcutRunner.execute` 里 `markJobRunning` 早于
        // `running.put`，中间有窗口 —— 那一刻 `cancel()` 拿不到进程（`killed=false`），
        // 状态却已是 running，于是 `abandoned` 也是 false → 界面回 `{"canceled": false}`、
        // **任务照跑到底** ✗（实测：发起渲染后立刻取消，两个 job 都 succeeded）。
        // 现在把这两种状态也认作"可放弃"：标成 canceled 后，
        // `execute` 会在登记进程后补一次检查并**真的杀掉**（见那里的注释）。
        String st = j.status();
        boolean abandoned = !killed && (Store.JOB_INTERRUPTED.equals(st)
                || Store.JOB_RUNNING.equals(st) || Store.JOB_QUEUED.equals(st));
        if (killed || abandoned) {
            store.finishJob(id, Store.JOB_CANCELED,
                    abandoned ? "已放弃（不再继续）" : "已取消", null, null, null);
        }
        ObjectNode out = mapper.createObjectNode();
        out.put("job_id", id);
        out.put("canceled", killed || abandoned);
        out.put("status", store.getJob(id).map(Store.JobRow::status).orElse(j.status()));
        return out;
    }

    @GetMapping(value = "/api/jobs/params", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode params() {
        return roughcut.defaults();
    }

    @GetMapping(value = "/api/jobs/health", produces = MediaType.APPLICATION_JSON_VALUE)
    public JsonNode health() {
        ObjectNode out = mapper.createObjectNode();
        out.set("roughcut", mapper.valueToTree(roughcut.describe()));
        out.put("generate_active", generate.activeCount());
        return out;
    }

    // ---------------- DTO ----------------

    private ObjectNode toDto(Store.JobRow j) {
        ObjectNode n = mapper.createObjectNode();
        n.put("id", j.id());
        n.put("kind", j.kind());
        n.put("status", j.status());
        if (KIND_ROUGHCUT.equals(j.kind()) && j.inputAssetId() != null) {
            n.put("input_asset_id", j.inputAssetId());
            assets.get(j.inputAssetId()).ifPresent(a -> n.put("input_name", a.name()));
        }
        if (KIND_GENERATE.equals(j.kind())) {
            ObjectNode params = readJson(j.paramsJson());
            n.put("material_id", params.path("material_id").asLong(0));
            n.set("platforms", params.path("platforms"));
            store.getMaterial(params.path("material_id").asLong(0))
                    .ifPresent(m -> n.put("input_name", m.title() == null ? m.coreMessage() : m.title()));
        }
        n.put("created_at", j.createdAt());
        if (KIND_SCENE_SPLIT.equals(j.kind()) && j.inputAssetId() != null) {
            // 与粗剪一样：界面上要能看到"这个任务是对哪个视频跑的"
            n.put("input_asset_id", j.inputAssetId());
            assets.get(j.inputAssetId()).ifPresent(a -> n.put("input_name", a.name()));
        }
        if (j.startedAt() != null) {
            n.put("started_at", j.startedAt());
        }
        if (j.finishedAt() != null) {
            n.put("finished_at", j.finishedAt());
        }
        if (j.error() != null && !j.error().isBlank()) {
            n.put("error", j.error());
        }
        // 「继续」按钮的开关：**只有 interrupted 给** —— failed 的任务没有可续的部分
        // （它的语义是"跑过了、结果错了"），该用 retry 从头重跑。
        n.put("can_resume", Store.JOB_INTERRUPTED.equals(j.status()));
        if (j.resultJson() != null && !j.resultJson().isBlank()) {
            n.set("result", readJson(j.resultJson()));
            List<String> failed = failedPlatforms(j);
            n.put("failed_count", failed.size());
            n.put("can_retry", !failed.isEmpty()
                    && !Store.JOB_RUNNING.equals(j.status()) && !Store.JOB_QUEUED.equals(j.status()));
        }

        JobProgress p = progressOf(j).orElseGet(JobProgress::empty);
        ObjectNode pn = n.putObject("progress");
        pn.put("stage", p.stage());
        if (p.pct() != null) {
            pn.put("pct", p.pct());
            pn.put("pct_stage", p.pctStage());
        }
        ArrayNode done = pn.putArray("done_stages");
        p.doneStages().forEach(done::add);
        pn.put("last_message", p.lastMessage());
        pn.put("stalled", p.stalled());

        ObjectNode outs = n.putObject("outputs");
        putUrl(outs, "video_url", j.outputPath());
        putUrl(outs, "srt_url", j.srtPath());
        putUrl(outs, "cuts_url", j.cutsPath());
        // 去冗余判定结果（`docs/spec_disfluency.md`）：用户选的是"先给方案人工确认"，
        // 那方案就得在界面上看得见 —— 不能只躺在任务日志里。
        if (j.workdir() != null && !j.workdir().isBlank()) {
            Path dedup = Path.of(j.workdir()).resolve("work/done/stage-去冗余.json");
            if (java.nio.file.Files.isRegularFile(dedup)) {
                putUrl(outs, "dedup_url", dedup.toString());
            }
        }
        // 切场景的**方案文件**：界面据此渲染缩略图网格（与 dedup_url 同一套 putUrl 机制，
        // 路径天然在 mediaDir 之内 —— workdir 就在 media 根下）
        if (KIND_SCENE_SPLIT.equals(j.kind()) && j.workdir() != null && !j.workdir().isBlank()) {
            Path plan = Path.of(j.workdir()).resolve("work/scene-plan.json");
            if (java.nio.file.Files.isRegularFile(plan)) {
                putUrl(outs, "plan_url", plan.toString());
            }
        }
        return n;
    }

    private java.util.Optional<JobProgress> progressOf(Store.JobRow j) {
        if (KIND_GENERATE.equals(j.kind())) {
            return generate.progress(j);
        }
        if (KIND_SCENE_SPLIT.equals(j.kind())) {
            return sceneSplit.progress(j);          // 事件格式与粗剪相同
        }
        return roughcut.progress(j);
    }

    private void putUrl(ObjectNode outs, String key, String absPath) {
        if (absPath == null || absPath.isBlank()) {
            return;
        }
        try {
            Path p = Path.of(absPath).toAbsolutePath().normalize();
            if (!p.startsWith(mediaDir.root())) {
                return;
            }
            outs.put(key, MediaController.mediaUrl(
                    mediaDir.root().relativize(p).toString().replace('\\', '/')));
        } catch (Exception ignored) {
            // 路径异常就当没有这个产物，不让详情接口崩
        }
    }

    private ObjectNode readJson(String raw) {
        if (raw == null || raw.isBlank()) {
            return mapper.createObjectNode();
        }
        try {
            JsonNode n = mapper.readTree(raw);
            return n.isObject() ? (ObjectNode) n : mapper.createObjectNode();
        } catch (Exception e) {
            return mapper.createObjectNode();
        }
    }
}
