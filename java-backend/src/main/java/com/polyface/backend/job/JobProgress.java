package com.polyface.backend.job;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * 从**阶段事件文件**里读出进度（M8 第一片）。
 *
 * <p>进度不是猜的：`scripts/roughcut.py` 已经在写一份带 `version` 的 JSONL
 * （见 `python-service/app/pipeline/runlog.py`），这里只负责读。
 * **这就是"Java 起子进程跑 CLI"这个选型的回报** —— 两者之间的契约是文件，不是新接口。
 *
 * <p>读的时候必须**容错**：事件文件可能不存在（任务刚起）、可能写到一半
 * （半行 JSON）、可能被杀在中间。这些都不该让详情接口崩掉，只是"进度未知"。
 */
public record JobProgress(String stage, Double pct, String pctStage, List<String> doneStages,
                          String lastMessage, boolean stalled) {

    private static final Logger log = LoggerFactory.getLogger(JobProgress.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    public static JobProgress empty() {
        return new JobProgress("", null, "", List.of(), "", false);
    }

    /**
     * @param running  任务是否还在跑（只有还在跑才谈得上"卡死"）
     * @param stallSec 多久没有新事件算疑似卡死
     */
    public static JobProgress read(Path eventsPath, boolean running, int stallSec) {
        if (eventsPath == null || !Files.isRegularFile(eventsPath)) {
            return empty();
        }
        Set<String> done = new LinkedHashSet<>();
        String current = "";
        Double pct = null;
        String pctStage = "";
        String lastMessage = "";
        try {
            for (String line : Files.readAllLines(eventsPath, StandardCharsets.UTF_8)) {
                if (line.isBlank()) {
                    continue;
                }
                JsonNode ev;
                try {
                    ev = MAPPER.readTree(line);
                } catch (Exception bad) {
                    continue;   // 半行 JSON：跳过，不炸
                }
                String stage = ev.path("stage").asText("");
                String kind = ev.path("kind").asText("");
                String message = ev.path("message").asText("");
                if (!message.isBlank()) {
                    lastMessage = message;
                }
                switch (kind) {
                    case "start" -> current = stage;
                    case "end", "fail" -> {
                        done.add(stage);
                        if (stage.equals(current)) {
                            current = "";
                        }
                    }
                    case "progress" -> {
                        // **不随阶段结束而清空**：否则轮询很容易落在"两个阶段之间"，
                        // 于是界面上明明在跑却看不到任何百分比（实测就是这么漏的）。
                        // 保留"最后一次已知进度 + 它属于哪个阶段"，由调用方决定怎么显示。
                        if (ev.path("fields").has("pct")) {
                            pct = ev.path("fields").path("pct").asDouble();
                            pctStage = stage;
                        }
                    }
                    default -> { }
                }
            }
        } catch (IOException e) {
            log.warn("读事件文件失败（进度按未知处理）：{}", e.getMessage());
            return empty();
        }
        return new JobProgress(current, pct, pctStage, new ArrayList<>(done), lastMessage,
                running && stalled(eventsPath, stallSec));
    }

    /** 疑似卡死：还在跑，但事件文件很久没动过。 */
    private static boolean stalled(Path eventsPath, int stallSec) {
        if (stallSec <= 0) {
            return false;
        }
        try {
            long idleMs = System.currentTimeMillis() - Files.getLastModifiedTime(eventsPath).toMillis();
            return idleMs > stallSec * 1000L;
        } catch (IOException e) {
            return false;
        }
    }
}
