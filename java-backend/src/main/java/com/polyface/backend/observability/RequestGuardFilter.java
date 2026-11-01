package com.polyface.backend.observability;

import java.io.IOException;
import java.util.Locale;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * 本地服务的**请求来源准入**（`openspec/changes/local-service-request-guard`）。
 *
 * <p><b>为什么需要</b>：本服务只监听回环、**没有鉴权**。"只听 127.0.0.1"拦不住浏览器里的任意网页：
 * <ul>
 *   <li><b>DNS rebinding</b> —— 攻击者域名先解析到自己、再改成 {@code 127.0.0.1}，
 *       浏览器就认为<b>同源</b>；CORS 此时完全不生效，页面能读能写；</li>
 *   <li><b>跨站简单请求</b> —— {@code <form>} / multipart 不需要预检，浏览器会真的发出去
 *       （只是读不到响应），足以<b>消耗用户付费的 LLM 额度</b>。</li>
 * </ul>
 * 实测（2026-10-31）：{@code curl -H "Host: evil.example.com" /api/materials} → <b>200 + 真实稿件</b>。
 *
 * <p>判据与 Python 侧同一套（见 {@code app/guard.py}、{@code docs/08} ADR-027）：
 * {@code Host} 必须回环；带 {@code Origin} 必须命中允许集合；<b>不带 {@code Origin} 放行</b>
 * （命令行与 Java→Python 的服务端调用都不带它，把它们当跨站会把正常链路打死）。
 *
 * <p><b>顺序</b>：比 {@link TraceFilter} 更靠前 —— 被拒的请求不该产生 trace。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestGuardFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(RequestGuardFilter.class);

    /** 回环主机名（不含端口）。 */
    private static final Set<String> LOOPBACK = Set.of("127.0.0.1", "localhost", "::1");

    /** 默认允许的 Origin 前缀：本机回环的任意端口（端口可配，枚举必漏）。 */
    private static final String[] LOOPBACK_ORIGIN_PREFIXES = {
            "http://127.0.0.1:", "https://127.0.0.1:",
            "http://localhost:", "https://localhost:",
            "http://[::1]:", "https://[::1]:",
    };

    private final boolean enabled;
    private final String allowedOrigins;

    public RequestGuardFilter(@Value("${polyface.guard.enabled:true}") boolean enabled,
                              @Value("${polyface.guard.allowed-origins:}") String allowedOrigins) {
        this.enabled = enabled;
        this.allowedOrigins = allowedOrigins == null ? "" : allowedOrigins;
    }

    /** {@code 127.0.0.1:8080} → {@code 127.0.0.1}；{@code [::1]:8080} → {@code ::1}。 */
    static String stripPort(String host) {
        String h = host == null ? "" : host.trim();
        if (h.startsWith("[")) {
            int end = h.indexOf(']');
            return end > 0 ? h.substring(1, end).toLowerCase(Locale.ROOT) : h.toLowerCase(Locale.ROOT);
        }
        int colon = h.indexOf(':');
        return (colon >= 0 ? h.substring(0, colon) : h).toLowerCase(Locale.ROOT);
    }

    static boolean hostAllowed(String host) {
        String h = stripPort(host);
        return !h.isEmpty() && LOOPBACK.contains(h);
    }

    boolean originAllowed(String origin) {
        if (origin == null || origin.isBlank()) {
            return false;
        }
        String o = origin.trim();
        if (o.equalsIgnoreCase("null")) {
            return false;   // 沙箱 iframe / file:// 正是跨站形态之一
        }
        for (String p : LOOPBACK_ORIGIN_PREFIXES) {
            if (o.startsWith(p)) {
                return true;
            }
        }
        for (String item : allowedOrigins.split(",")) {
            String it = item.trim();
            if (it.endsWith("/")) {
                it = it.substring(0, it.length() - 1);
            }
            if (!it.isEmpty() && o.equals(it)) {
                return true;
            }
        }
        return false;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        if (!enabled) {
            chain.doFilter(request, response);
            return;
        }
        String host = request.getHeader("Host");
        String origin = request.getHeader("Origin");

        String reason = null;
        if (!hostAllowed(host)) {
            reason = "Host 不是回环（" + (host == null || host.isBlank() ? "(空)" : host)
                    + "）：疑似 DNS rebinding";
        } else if (origin != null && !origin.isBlank() && !originAllowed(origin)) {
            reason = "Origin 不在允许集合（" + origin + "）：疑似跨站请求";
        }
        if (reason == null) {
            chain.doFilter(request, response);
            return;
        }

        log.warn("拒绝请求（{}）：{} {} host={} origin={}",
                reason, request.getMethod(), request.getRequestURI(), host, origin);
        response.setStatus(HttpServletResponse.SC_FORBIDDEN);
        response.setContentType("text/plain; charset=utf-8");
        response.getWriter().write(
                "403 请求来源不被允许。\n"
                + "原因：" + reason + "\n"
                + "本服务只接受本机（127.0.0.1 / localhost）发起的请求。\n"
                + "如果你是把工作台反代到了别的域名，请设置 POLYFACE_ALLOWED_ORIGINS"
                + "（逗号分隔，例如 POLYFACE_ALLOWED_ORIGINS=https://my.example.com）。\n");
    }
}
