from samplers.base_sampler import BaseSampler, SampleResult
from samplers.athena_sampler import AthenaSampler
from samplers.s3_sampler import S3Sampler


def get_sampler(data_source_type: str, staging_bucket: str, **kwargs) -> BaseSampler:
    """Return the right sampler for a given data_source_type."""
    match data_source_type:
        case "athena" | "glue":
            return AthenaSampler(
                staging_bucket=staging_bucket,
                athena_output_location=kwargs.get(
                    "athena_output_location", f"s3://{staging_bucket}/athena-results/"
                ),
                aws_region=kwargs.get("aws_region", "us-east-1"),
            )
        case "s3_parquet" | "s3_csv" | "s3_json":
            return S3Sampler(staging_bucket=staging_bucket)
        case _:
            raise ValueError(f"No sampler available for data_source_type={data_source_type!r}")
