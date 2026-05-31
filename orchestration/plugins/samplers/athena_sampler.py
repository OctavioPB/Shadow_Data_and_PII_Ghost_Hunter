import logging
import time

import boto3
import pandas as pd

from samplers.base_sampler import BaseSampler, SampleResult

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 2
_QUERY_TIMEOUT_S = 300


class AthenaSampler(BaseSampler):
    """
    Samples up to MAX_ROWS rows from an AWS Athena / Glue table.

    Executes a read-only SELECT with ORDER BY rand() LIMIT — never writes
    to the source database or catalog.
    """

    def __init__(
        self,
        staging_bucket: str,
        athena_output_location: str,
        aws_region: str = "us-east-1",
    ) -> None:
        super().__init__(staging_bucket)
        self.athena_output_location = athena_output_location
        self.aws_region = aws_region

    def sample(self, source_name: str, table_id: str, **kwargs) -> SampleResult:
        """
        source_name must be in `database.table` format.
        Runs: SELECT * FROM database.table ORDER BY rand() LIMIT 1000
        """
        parts = source_name.split(".", 1)
        database, table = (parts[0], parts[1]) if len(parts) == 2 else ("default", parts[0])

        query = (
            f"SELECT * FROM {database}.{table} ORDER BY rand() LIMIT {self.MAX_ROWS}"  # noqa: S608
        )

        athena = boto3.client("athena", region_name=self.aws_region)
        response = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": database},
            ResultConfiguration={"OutputLocation": self.athena_output_location},
        )
        execution_id: str = response["QueryExecutionId"]
        logger.info("Athena query started: %s for %s", execution_id, source_name)

        df = self._wait_and_fetch(athena, execution_id)
        s3_path = self._write_sample_to_staging(df, table_id)

        return SampleResult(
            source_name=source_name,
            table_id=table_id,
            columns=self._extract_column_meta(df),
            sample_s3_path=s3_path,
            row_count=len(df),
        )

    def _wait_and_fetch(self, athena, execution_id: str) -> pd.DataFrame:
        deadline = time.monotonic() + _QUERY_TIMEOUT_S
        while time.monotonic() < deadline:
            status_resp = athena.get_query_execution(QueryExecutionId=execution_id)
            state: str = status_resp["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                reason = status_resp["QueryExecution"]["Status"].get("StateChangeReason", "")
                raise RuntimeError(f"Athena query {execution_id} {state}: {reason}")
            time.sleep(_POLL_INTERVAL_S)
        else:
            raise TimeoutError(
                f"Athena query {execution_id} timed out after {_QUERY_TIMEOUT_S}s"
            )

        results = athena.get_query_results(QueryExecutionId=execution_id)
        metadata = results["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        columns = [col["Label"] for col in metadata]

        rows = [
            [cell.get("VarCharValue", "") for cell in row["Data"]]
            for row in results["ResultSet"]["Rows"][1:]  # skip header row
        ]
        return pd.DataFrame(rows, columns=columns)
