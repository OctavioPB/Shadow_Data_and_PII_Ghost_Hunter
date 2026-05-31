import io
import logging

import boto3
import pandas as pd

from samplers.base_sampler import BaseSampler, SampleResult

logger = logging.getLogger(__name__)

_FORMAT_READERS = {
    "parquet":    pd.read_parquet,
    "s3_parquet": pd.read_parquet,
    "csv":        lambda b: pd.read_csv(io.BytesIO(b)),
    "s3_csv":     lambda b: pd.read_csv(io.BytesIO(b)),
    "json":       lambda b: pd.read_json(io.BytesIO(b), lines=True),
    "s3_json":    lambda b: pd.read_json(io.BytesIO(b), lines=True),
}


class S3Sampler(BaseSampler):
    """
    Samples up to MAX_ROWS rows from an S3 object (Parquet, CSV, or JSON).

    source_name must be an S3 URI: `s3://bucket/path/to/file.ext`.
    The source object is opened read-only via s3.get_object — never written to.
    """

    def sample(self, source_name: str, table_id: str, **kwargs) -> SampleResult:
        file_format: str = kwargs.get("file_format", "parquet")

        bucket, key = self._parse_s3_uri(source_name)

        s3 = boto3.client("s3")
        logger.info("Fetching s3://%s/%s for sampling", bucket, key)
        obj = s3.get_object(Bucket=bucket, Key=key)
        body: bytes = obj["Body"].read()

        df = self._deserialize(body, file_format)

        if len(df) > self.MAX_ROWS:
            df = df.sample(n=self.MAX_ROWS, random_state=42).reset_index(drop=True)

        s3_path = self._write_sample_to_staging(df, table_id)

        return SampleResult(
            source_name=source_name,
            table_id=table_id,
            columns=self._extract_column_meta(df),
            sample_s3_path=s3_path,
            row_count=len(df),
        )

    @staticmethod
    def _parse_s3_uri(uri: str) -> tuple[str, str]:
        without_scheme = uri.removeprefix("s3://")
        bucket, _, key = without_scheme.partition("/")
        if not bucket or not key:
            raise ValueError(f"Invalid S3 URI: {uri!r}")
        return bucket, key

    @staticmethod
    def _deserialize(body: bytes, file_format: str) -> pd.DataFrame:
        reader = _FORMAT_READERS.get(file_format)
        if reader is None:
            raise ValueError(f"Unsupported file format: {file_format!r}")
        return reader(io.BytesIO(body) if file_format in ("parquet", "s3_parquet") else body)
