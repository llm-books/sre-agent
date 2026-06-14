// k6 load generator. Runs as the grafana/k6 container, so k6 does not need to be
// installed locally. Produces a diurnal-ish shape by ramping virtual users up and
// down, with the occasional spike, so the services show realistic baseline noise
// rather than a flat synthetic line. Traffic enters at the web tier, which fans
// out through the gateway to orders, payments, and inventory.
import http from "k6/http";
import { sleep } from "k6";

const WEB = __ENV.WEB_URL || "http://web:8080";

export const options = {
  scenarios: {
    diurnal: {
      executor: "ramping-vus",
      startVUs: 2,
      stages: [
        { duration: "2m", target: 8 },   // morning ramp
        { duration: "5m", target: 15 },  // midday plateau
        { duration: "1m", target: 40 },  // a spike (flash sale)
        { duration: "3m", target: 12 },  // settle
        { duration: "5m", target: 6 },   // evening taper
        { duration: "2m", target: 2 },   // overnight
      ],
      gracefulRampDown: "10s",
    },
  },
};

export default function () {
  // A checkout drives the full dependency chain: web -> gateway -> orders ->
  // payments + inventory. A fault injected in any of those surfaces here.
  http.get(`${WEB}/checkout`);
  sleep(Math.random() * 1.5);
}
