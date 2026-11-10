# The upstream collector image is distroless, so compose cannot healthcheck it. Copy the same pinned
# binary onto alpine, which brings busybox wget for the health_check probe (F-2).
FROM otel/opentelemetry-collector-contrib:0.165.0 AS upstream

FROM alpine:3.24.4
RUN adduser -D -H -u 10001 otel
COPY --from=upstream /otelcol-contrib /otelcol-contrib
USER 10001
ENTRYPOINT ["/otelcol-contrib"]
