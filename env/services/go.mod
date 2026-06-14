module sre-agent/env/services

go 1.22

// The rest of the dependency graph is resolved by `go mod tidy`, which the
// Dockerfile runs during the build. Only the direct dependency is pinned here.
require github.com/prometheus/client_golang v1.19.1
