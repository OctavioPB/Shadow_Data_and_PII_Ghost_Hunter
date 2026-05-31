import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

MAX_ROWS = 1_000


@dataclass
class ColumnMeta:
    name: str
    dtype: str
    sample_count: int


@dataclass
class SampleResult:
    source_name: str
    table_id: str
    columns: list[ColumnMeta]
    sample_s3_path: str
    row_count: int
    extra: dict = field(default_factory=dict)


class BaseSampler(ABC):
    """
    Abstract base for all PII samplers.

    Subclasses must implement `sample()`. Writes are restricted to the
    staging bucket — source tables/objects are never modified.
    """

    MAX_ROWS = MAX_ROWS

    def __init__(self, staging_bucket: str, staging_prefix: str = "samples") -> None:
        self.staging_bucket = staging_bucket
        self.staging_prefix = staging_prefix

    @abstractmethod
    def sample(self, source_name: str, table_id: str, **kwargs) -> SampleResult:
        """Draw up to MAX_ROWS rows from the source and return a SampleResult."""

    def _extract_column_meta(self, df: pd.DataFrame) -> list[ColumnMeta]:
        return [
            ColumnMeta(
                name=col,
                dtype=str(df[col].dtype),
                sample_count=int(df[col].notna().sum()),
            )
            for col in df.columns
        ]

    def _write_sample_to_staging(self, df: pd.DataFrame, table_id: str) -> str:
        """Write sample DataFrame to the staging bucket as Parquet. Returns S3 URI."""
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pandas(df), buf)
        buf.seek(0)

        s3_key = f"{self.staging_prefix}/{table_id}/sample.parquet"
        s3 = boto3.client("s3")
        s3.put_object(Bucket=self.staging_bucket, Key=s3_key, Body=buf.getvalue())

        logger.info("Sample written to s3://%s/%s (%d rows)", self.staging_bucket, s3_key, len(df))
        return f"s3://{self.staging_bucket}/{s3_key}"
