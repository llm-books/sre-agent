# Build the single configurable service binary. The same image runs as all six
# services; SERVICE_NAME and friends are set per instance in docker-compose.yml.
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY main.go ./
# go.sum is generated here rather than committed, to keep the scaffold light.
# The first build needs network access to resolve the dependency graph.
RUN go mod tidy
RUN CGO_ENABLED=0 go build -o /bin/sre-service .

FROM alpine:3.20
RUN adduser -D -u 10001 app
USER app
COPY --from=build /bin/sre-service /bin/sre-service
# All six instances export traces to Tempo over OTLP/HTTP (ch09). Override per
# instance in docker-compose if needed.
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318
EXPOSE 8080
ENTRYPOINT ["/bin/sre-service"]
