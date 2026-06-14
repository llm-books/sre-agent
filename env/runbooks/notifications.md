# Runbook: notifications

Quality: STALE. Predates the move to batch mode. Treat with suspicion.

## Symptom
Customers report missing order confirmation emails.

## Checks
The notifications service exposes an alert on email send failures. If emails are
missing, check for the `NotificationSendErrors` alert and look for 5xx from the
email provider.

> Note from a later on-call: the above is out of date. After the switch to batch
> mode there is no per-send error alert, and a stalled worker produces NO alert
> at all, only a growing queue. If confirmations are missing and nothing is
> alerting, check `worker_queue_depth` directly. A flat-then-climbing queue with
> no errors means the worker stopped processing. Restart the worker.
