from pydantic_settings import BaseSettings, SettingsConfigDict


class ScannerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_consumer_group: str = "pii-scanner"
    kafka_schema_registry_url: str = "http://localhost:8081"

    # Topics
    topic_table_created: str = "table.created"
    topic_file_moved: str = "file.moved"
    topic_pii_candidates: str = "pii.candidates"
    topic_dlq_suffix: str = ".dlq"

    # Database (sync, for scanner daemon)
    database_url: str = "postgresql://airflow:airflow@localhost:5432/pii_hunter"

    # Observability
    metrics_port: int = 8888
