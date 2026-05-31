from prometheus_client import Counter, Histogram, start_http_server

events_consumed_total = Counter(
    "scanner_events_consumed_total",
    "Total Kafka events successfully consumed and processed",
    ["topic", "consumer_group"],
)

events_dlq_total = Counter(
    "scanner_events_dlq_total",
    "Total events routed to the dead-letter topic due to parse/validation errors",
    ["topic"],
)

event_processing_seconds = Histogram(
    "scanner_event_processing_seconds",
    "End-to-end processing time per event (parse + persist + publish)",
    ["topic"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)
