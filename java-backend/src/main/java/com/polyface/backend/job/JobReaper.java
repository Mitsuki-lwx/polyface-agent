package com.polyface.backend.job;

import java.util.Collection;
import java.util.function.Supplier;

/**
 * job 子进程的**退出清理**。
 *
 * <p><b>为什么需要它</b>（2026-10-27 实测踩到）：杀掉 Java **不会**带走它拉起的 job 子进程 ——
 * Windows 的 {@code Stop-Process} 不杀进程树，孤儿进程会继续把活儿跑完、
 * 继续调 LLM（花额度），而数据库里那条任务已经是 {@code interrupted} ——
 * 「<b>活儿干完了、账记成中断了</b>」✗。
 *
 * <p>当时的现场：粗剪跑到「找停顿」时杀掉 Java，重启后 {@code work/done/} 里却是**全阶段**；
 * 查时间戳发现 {@code stage-转写.json} 比杀晚 **59.5 秒**才写入 ✗。
 *
 * <p><b>为什么是"补上"而不是"新发明"</b>：编辑器那边早有同样的钩子
 * （{@code EditorProcess} 的 {@code addShutdownHook("gimpish-shutdown")}），
 * job 侧一直缺 —— 两处职责相同、行为却不一样 ✗。
 *
 * <p>注意：这里**只负责杀**。数据库状态由各 Runner 启动时的
 * {@code recoverInterrupted()} 收拾（它会把这些任务标成"可继续"）。
 * 两件事分开，是因为"杀进程"必须在退出那一刻做，而"改状态"下次启动做更稳。
 */
final class JobReaper {

    private JobReaper() {
    }

    /**
     * 装一个退出钩子：JVM 退出时把 {@code live} 里的进程**连同其子孙**强杀。
     *
     * @param name 钩子线程名（便于排查）
     * @param live 返回"当前还在跑的进程"的取值器 —— 传取值器而不是快照，
     *             这样钩子执行时看到的是**那一刻**还在跑的（期间可能有新的进来）
     */
    static void install(String name, Supplier<Collection<Process>> live) {
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            for (Process p : live.get()) {
                try {
                    // 先杀子孙再杀自己：反过来的话父进程一死，子孙就成孤儿、更难收
                    p.descendants().forEach(ProcessHandle::destroyForcibly);
                    p.destroyForcibly();
                } catch (Exception ignored) {
                    // 退出路径上**不抛**：钩子里抛异常只会让退出更难看，且我们无能为力
                }
            }
        }, name));
    }
}
