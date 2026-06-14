module sre-agent/env/services

go 1.22

// The rest of the dependency graph is resolved by `go mod tidy`, which the
// Dockerfile runs during the build. Only the direct dependencies are pinned here.
require (
	github.com/prometheus/client_golang v1.19.1
	go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.53.0
	go.opentelemetry.io/otel v1.28.0
	go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp v1.28.0
	go.opentelemetry.io/otel/sdk v1.28.0
)
