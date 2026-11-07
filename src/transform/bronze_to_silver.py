import os
import re
import json
import hashlib
import logging
import boto3
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

# 1. CÁC HÀM BẢO MẬT PII & CHUẨN HÓA DỮ LIỆU
def hash_pii_sha256(value: str) -> str:
    """Băm một chiều SHA-256 cho thông tin nhạy cảm (Email HR)."""
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()

def mask_phone(phone: str) -> str:
    """Che số điện thoại: 0988111222 -> 098****222."""
    if not phone or len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-3:]}"

def parse_salary_to_vnd(salary_raw: str) -> tuple:
    """Tách chuỗi lương ('15 - 25 triệu') thành (min_vnd, max_vnd)."""
    if not salary_raw:
        return (0, 0)
    numbers = [int(n) for n in re.findall(r"\d+", str(salary_raw))]
    if len(numbers) >= 2:
        # Nếu đơn vị là triệu (nhỏ hơn 1000), nhân với 1.000.000
        factor = 1_000_000 if numbers[0] < 1000 else 1
        return (numbers[0] * factor, numbers[1] * factor)
    elif len(numbers) == 1:
        factor = 1_000_000 if numbers[0] < 1000 else 1
        val = numbers[0] * factor
        return (val, val)
    return (0, 0)

# 2. ĐỌC FILE MỚI NHẤT TỪ S3 BRONZE
def get_latest_bronze_data() -> list:
    s3_client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://s3:4566"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    )
    bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze-it-jobs")
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix="raw_jobs/")
    objects = response.get("Contents", [])
    if not objects:
        raise FileNotFoundError("Không tìm thấy dữ liệu trong tầng Bronze S3!")

    latest_obj = max(objects, key=lambda x: x["LastModified"])
    latest_key = latest_obj["Key"]
    logger.info(f"Đang đọc file mới nhất từ Bronze S3: s3://{bucket}/{latest_key}")

    body = s3_client.get_object(Bucket=bucket, Key=latest_key)["Body"].read().decode("utf-8")
    return json.loads(body)

# 3. KIỂM ĐỊNH CHẤT LƯỢNG (DATA QUALITY GATE) & GHI VÀO POSTGRESQL
def process_bronze_to_silver():
    raw_records = get_latest_bronze_data()
    valid_rows = []
    quarantine_rows = []

    for rec in raw_records:
        errors = []
        job_id = rec.get("job_id")
        job_title = rec.get("job_title")
        company_name = rec.get("company_name")
        min_sal, max_sal = parse_salary_to_vnd(rec.get("salary_raw"))

        # Quy tắc Data Quality Gate
        if not job_id:
            errors.append("MISSING_JOB_ID")
        if not job_title or not company_name:
            errors.append("MISSING_TITLE_OR_COMPANY")
        if min_sal < 0 or max_sal < 0 or max_sal < min_sal:
            errors.append(f"INVALID_SALARY_RANGE ({min_sal} > {max_sal})")

        if errors:
            quarantine_rows.append((
                json.dumps(rec, ensure_ascii=False),
                ", ".join(errors)
            ))
        else:
            skills_clean = [str(s).strip().lower() for s in rec.get("skills", [])]
            valid_rows.append((
                str(job_id).strip(),
                str(job_title).strip(),
                str(company_name).strip(),
                str(rec.get("location", "Remote")).strip(),
                min_sal,
                max_sal,
                skills_clean,
                hash_pii_sha256(rec.get("hr_email")),
                mask_phone(rec.get("hr_phone")),
                rec.get("posted_date"),
                rec.get("source", "unknown")
            ))

    # Kết nối PostgreSQL bằng tài khoản phân quyền hạn chế (airflow_etl_user)
    conn = psycopg2.connect(
        host=os.getenv("DW_HOST", "postgres"),
        port=5432,
        dbname=os.getenv("DW_DB_NAME", "it_jobs_dw"),
        user=os.getenv("ETL_USER", "airflow_etl_user"),
        password=os.getenv("ETL_PASSWORD", "etl_secret_pass_123")
    )

    try:
        with conn, conn.cursor() as cur:
            # Upsert dữ liệu sạch vào bảng silver.clean_jobs (Đảm bảo Idempotency)
            if valid_rows:
                upsert_silver_sql = """
                    INSERT INTO silver.clean_jobs (
                        job_id, job_title, company_name, location,
                        min_salary_vnd, max_salary_vnd, skills,
                        hr_email_sha256, hr_phone_masked, posted_date, source
                    ) VALUES %s
                    ON CONFLICT (job_id) DO UPDATE SET
                        job_title = EXCLUDED.job_title,
                        min_salary_vnd = EXCLUDED.min_salary_vnd,
                        max_salary_vnd = EXCLUDED.max_salary_vnd,
                        skills = EXCLUDED.skills,
                        ingested_at = CURRENT_TIMESTAMP;
                """
                execute_values(cur, upsert_silver_sql, valid_rows)

            # Đẩy dữ liệu lỗi vào bảng cách ly quarantine.rejected_jobs
            if quarantine_rows:
                insert_quarantine_sql = """
                    INSERT INTO quarantine.rejected_jobs (raw_payload, error_reasons)
                    VALUES %s;
                """
                execute_values(cur, insert_quarantine_sql, quarantine_rows)

        logger.info(
            f"Hoàn tất Tầng Silver: {len(valid_rows)} bản ghi HỢP LỆ -> silver.clean_jobs | "
            f"{len(quarantine_rows)} bản ghi LỖI -> quarantine.rejected_jobs"
        )
    finally:
        conn.close()

if __name__ == "__main__":
    process_bronze_to_silver()