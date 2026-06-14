// Command sre-service is a single configurable microservice, run as six named
// instances to model a small e-commerce checkout: web, api-gateway, orders,
// payments, inventory, notifications.
//
// It is deliberately small. Its job is not to be a real service but to produce
// realistic telemetry and to fail in realistic ways on command, so the SRE
// agent has something true to diagnose. The chaos engine injects faults by
// POSTing to /admin/fault; nothing here needs a restart to start misbehaving.
//
// Tracing is intentionally absent at this checkpoint. The services gain
// OpenTelemetry trace emission in the chapter 9 build, which is where the book
// takes up observability. Until then they expose Prometheus metrics and emit
// structured JSON logs, which Loki collects.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math/rand"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// initTracer wires OpenTelemetry tracing to Tempo over OTLP/HTTP. Added in the
// chapter 9 build: the services start emitting traces here, so the agent's
// trace_lookup tool returns real service traces. The endpoint comes from
// OTEL_EXPORTER_OTLP_ENDPOINT. Best-effort: if the exporter can't start, the
// service runs untraced rather than failing.
func initTracer(ctx context.Context, name string, log *slog.Logger) func() {
	exp, err := otlptracehttp.New(ctx)
	if err != nil {
		log.Warn("tracing disabled", "err", err.Error())
		return func() {}
	}
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exp),
		sdktrace.WithResource(resource.NewSchemaless(
			attribute.String("service.name", name))),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	return func() { _ = tp.Shutdown(context.Background()) }
}

// faults holds the injectable failure state. The chaos engine flips these.
type faults struct {
	mu             sync.RWMutex
	latencyMillis  int     // extra latency added to every request
	errorRate      float64 // 0..1 chance a request returns 5xx
	depTimeout     bool    // calls to downstream dependencies hang then fail
	stopProcessing bool    // worker stops draining its queue (silent failure)
	memLeakOn      bool    // background memory growth
}

func (f *faults) snapshot() (int, float64, bool, bool, bool) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return f.latencyMillis, f.errorRate, f.depTimeout, f.stopProcessing, f.memLeakOn
}

func (f *faults) set(field string, value any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch field {
	case "latency_millis":
		if v, ok := toInt(value); ok {
			f.latencyMillis = v
		}
	case "error_rate":
		if v, ok := value.(float64); ok {
			f.errorRate = v
		}
	case "dependency_timeout":
		if v, ok := value.(bool); ok {
			f.depTimeout = v
		}
	case "stop_processing":
		if v, ok := value.(bool); ok {
			f.stopProcessing = v
		}
	case "memory_leak":
		if v, ok := value.(bool); ok {
			f.memLeakOn = v
		}
	}
}

func (f *faults) reset() {
	f.mu.Lock()
	defer f.mu.Unlock()
	// Reset the fields individually. Do NOT do `*f = faults{}`: that would
	// replace the embedded mutex itself with a fresh (unlocked) one, and the
	// deferred Unlock would then fatal with "Unlock of unlocked RWMutex".
	f.latencyMillis = 0
	f.errorRate = 0
	f.depTimeout = false
	f.stopProcessing = false
	f.memLeakOn = false
}

func toInt(v any) (int, bool) {
	switch n := v.(type) {
	case float64:
		return int(n), true
	case int:
		return n, true
	}
	return 0, false
}

func main() {
	name := env("SERVICE_NAME", "service")
	port := env("PORT", "8080")
	deps := splitDeps(env("DEPENDENCIES", ""))
	isWorker := env("ROLE", "") == "worker"

	log := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", name)

	shutdown := initTracer(context.Background(), name, log)
	defer shutdown()

	reqs := promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_requests_total",
		Help: "Total HTTP requests handled.",
	}, []string{"service", "code"})
	dur := promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "Request duration in seconds.",
		Buckets: prometheus.DefBuckets,
	}, []string{"service"})
	queueDepth := promauto.NewGauge(prometheus.GaugeOpts{
		Name: "worker_queue_depth",
		Help: "Pending items in the worker queue. Grows when processing stops.",
		ConstLabels: prometheus.Labels{"service": name},
	})
	leakBytes := promauto.NewGauge(prometheus.GaugeOpts{
		Name: "process_leaked_bytes",
		Help: "Simulated leaked memory in bytes.",
		ConstLabels: prometheus.Labels{"service": name},
	})

	f := &faults{}

	// Background worker: drains a queue unless processing is stopped. This is the
	// notifications service in the silent-failure scenario, where the queue grows
	// with no alert because nothing is slow or erroring, just unprocessed.
	if isWorker {
		go func() {
			depth := 0.0
			tick := time.NewTicker(1 * time.Second)
			defer tick.Stop()
			for range tick.C {
				_, _, _, stopped, _ := f.snapshot()
				depth += 5 // new work always arrives
				if !stopped {
					depth -= 5 // and is processed, so depth stays flat
				}
				if depth < 0 {
					depth = 0
				}
				queueDepth.Set(depth)
			}
		}()
	}

	// Background memory leak: grows a retained buffer while the fault is on.
	go func() {
		var retained [][]byte
		tick := time.NewTicker(2 * time.Second)
		defer tick.Stop()
		for range tick.C {
			_, _, _, _, leak := f.snapshot()
			if leak {
				retained = append(retained, make([]byte, 1<<20)) // +1 MiB
			}
			leakBytes.Set(float64(len(retained)) * float64(1<<20))
		}
	}()

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())

	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		// Health stays green during a silent failure on purpose. A growing queue
		// is not an unhealthy process, which is the whole point of that scenario.
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})

	mux.HandleFunc("/admin/fault", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		for k, v := range body {
			f.set(k, v)
		}
		log.Info("fault injected", "fault", body)
		w.WriteHeader(http.StatusNoContent)
	})

	mux.HandleFunc("/admin/reset", func(w http.ResponseWriter, r *http.Request) {
		f.reset()
		log.Info("faults reset")
		w.WriteHeader(http.StatusNoContent)
	})

	// The business handler. Simulates work, applies injected faults, and calls
	// downstream dependencies so a fault in one service shows up as a symptom in
	// the services that depend on it (the cascade).
	handle := func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		latency, errRate, depTimeout, _, _ := f.snapshot()

		base := 5 + rand.Intn(15) // 5-20ms of honest work
		time.Sleep(time.Duration(base+latency) * time.Millisecond)

		code := http.StatusOK
		if errRate > 0 && rand.Float64() < errRate {
			code = http.StatusInternalServerError
		}

		if code == http.StatusOK {
			if err := callDeps(r.Context(), deps, depTimeout); err != nil {
				code = http.StatusBadGateway
				log.Warn("downstream call failed", "err", err.Error())
			}
		}

		dur.WithLabelValues(name).Observe(time.Since(start).Seconds())
		reqs.WithLabelValues(name, statusClass(code)).Inc()
		w.WriteHeader(code)
		_, _ = w.Write([]byte(name + " " + http.StatusText(code)))
	}
	mux.HandleFunc("/", handle)
	mux.HandleFunc("/checkout", handle)

	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           otelhttp.NewHandler(mux, "http.server"),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Info("starting", "port", port, "dependencies", deps, "worker", isWorker)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Error("server stopped", "err", err.Error())
		os.Exit(1)
	}
}

// callDeps fans out to downstream services. A dependency timeout makes the call
// hang past a short client deadline and then fail, which is how the payments
// provider-timeout scenario propagates upstream.
func callDeps(ctx context.Context, deps []string, depTimeout bool) error {
	// otelhttp transport propagates the trace context to downstream services, so a
	// checkout flows web -> gateway -> orders -> payments/inventory as one trace.
	client := &http.Client{
		Timeout:   3 * time.Second,
		Transport: otelhttp.NewTransport(http.DefaultTransport),
	}
	for _, url := range deps {
		reqCtx := ctx
		if depTimeout {
			time.Sleep(4 * time.Second) // exceeds the 3s client timeout below
		}
		req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, url, nil)
		if err != nil {
			return err
		}
		resp, err := client.Do(req)
		if err != nil {
			return err
		}
		_ = resp.Body.Close()
		if resp.StatusCode >= 500 {
			return errors.New("downstream " + url + " returned " + resp.Status)
		}
	}
	return nil
}

func statusClass(code int) string {
	switch {
	case code >= 500:
		return "5xx"
	case code >= 400:
		return "4xx"
	default:
		return "2xx"
	}
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func splitDeps(s string) []string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := parts[:0]
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
