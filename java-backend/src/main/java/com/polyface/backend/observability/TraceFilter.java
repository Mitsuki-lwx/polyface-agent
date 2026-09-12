package com.polyface.backend.observability;

import java.io.IOException;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * 每个请求生成 trace_id 并写入响应头（FR-71）。
 *
 * <p>统一在此管理生命周期，避免逐个 Controller 手工 startNew/clear ——
 * 后者极易漏掉 finally 导致线程复用串号。
 *
 * <p>响应头 {@code X-Trace-Id} 供前端展示与问题排查；同一 id 会随
 * {@code PythonClient} 传给 Python 服务，形成完整链路。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        try {
            String traceId = TraceContext.startNew();
            response.setHeader(TraceContext.HEADER, traceId);
            chain.doFilter(request, response);
        } finally {
            TraceContext.clear();
        }
    }
}
