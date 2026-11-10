# The upstream collector image is distroless, so compose cannot healthcheck it. Copy the same pinned
# binary onto alpine, which brings busybox wget for the health_check probe (F-2).
FROM otel/opentelemetry-collector-contrib:0.165.0@sha256:e40d61f10c9a617cca1ffd1be4d7e2b3a4b0f0001798ff07e58cabe82e618cea AS upstream

FROM alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN adduser -D -H -u 10001 otel
COPY --from=upstream /otelcol-contrib /otelcol-contrib
USER 10001
ENTRYPOINT ["/otelcol-contrib"]
