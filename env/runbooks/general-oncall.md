# Runbook: general on-call

Quality: vague. Better than nothing, not by much.

When you get paged:
- Look at the dashboards.
- Figure out what changed.
- Fix it or escalate.

For anything involving the api-gateway, remember it is the auth path for every
service, so changes there have a broad blast radius. Roll back gateway config
changes carefully and with approval.
