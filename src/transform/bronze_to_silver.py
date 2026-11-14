import os
import re
import json
import hashlib
import logging
import boto3
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

USD_TO_VND_RATE = 25400

def hash_pii_sha256(value: str) -> str:
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()

def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-3:]}"

def parse_salary_to_vnd(salary_raw: str, source: str = "") -> tuple:
    if not salary_raw:
        return (15_000_000, 25_000_000)
    s = str(salary_raw).lower().replace(",", "")
    s = re.sub(r"(\d+)\s*k\b", lambda m: str(int(m.group(1)) * 1000), s)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if not nums:
        return (15_000_000, 25_000_000)
    low, high = nums[0], (nums[1] if len(nums) >= 2 else nums[0])
    is_usd = ("$" in s) or ("usd" in s) or ("global" in source and "triệu" not in s)

    if is_usd:
        if any(w in s for w in ["hour", "hr", "/h"]) or high < 200:
            low_usd_month, high_usd_month = low * 160, high * 160
        elif any(w in s for w in ["year", "yr", "annual"]) or high >= 12000:
            low_usd_month, high_usd_month = low / 12.0, high / 12.0
        else:
            low_usd_month, high_usd_month = low, high
        return (int(low_usd_month * USD_TO_VND_RATE), int(high_usd_month * USD_TO_VND_RATE))
    else:
        factor = 1_000_000 if high < 1000 else 1
        return (int(low * factor), int(high * factor))

def get_latest_bronze_data() -> list:
    s3_client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://s3:4566"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    )
    bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze-it-jobs")
    objects = s3_client.list_objects_v2(Bucket=bucket, Prefix="raw_jobs/").get("Contents", [])
    if not objects:
        raise FileNotFoundError("Không tìm thấy dữ liệu trong tầng Bronze S3!")
    latest_key = max(objects, key=lambda x: x["LastModified"])["Key"]
    body = s3_client.get_object(Bucket=bucket, Key=latest_key)["Body"].read().decode("utf-8")
    return json.loads(body)

def process_bronze_to_silver():
    raw_records = get_latest_bronze_data()
    valid_rows, quarantine_rows = [], []

    for rec in raw_records:
        errors = []
        job_id = rec.get("job_id")
        job_title = rec.get("job_title")
        company_name = rec.get("company_name")
        source = rec.get("source", "unknown")
        min_yoe = int(rec.get("min_yoe", 0))
        ilo_code = int(rec.get("ilo_group_code", 2))
        min_sal, max_sal = parse_salary_to_vnd(rec.get("salary_raw"), source)

        if not job_id:
            errors.append("MISSING_JOB_ID")
        if not job_title or not company_name:
            errors.append("MISSING_TITLE_OR_COMPANY")
        if min_sal <= 0 or max_sal <= 0 or max_sal < min_sal:
            errors.append(f"INVALID_SALARY_RANGE ({min_sal} > {max_sal})")
        if min_yoe < 0:
            errors.append(f"NEGATIVE_YOE ({min_yoe})")
        if not (1 <= ilo_code <= 10):
            errors.append(f"INVALID_ILO_GROUP ({ilo_code})")

        if errors:
            quarantine_rows.append((json.dumps(rec, ensure_ascii=False), ", ".join(errors)))
        else:
            skills_clean = list({str(s).strip().lower() for s in rec.get("skills", []) if str(s).strip()})
            valid_rows.append((
                str(job_id).strip(),
                str(job_title).strip(),
                ilo_code,
                str(rec.get("ilo_major_group", "2. Nhóm chuyên môn bậc cao")).strip(),
                str(rec.get("base_occupation", "Chuyên môn tổng hợp")).strip(),
                str(company_name).strip(),
                str(rec.get("location", "Remote")).strip()[:100],
                str(rec.get("experience_level", "Mid-level")).strip(),
                min_yoe,
                str(rec.get("language_req", "None")).strip(),
                str(rec.get("work_model", "Onsite")).strip(),
                min_sal,
                max_sal,
                json.dumps(skills_clean, ensure_ascii=False),
                hash_pii_sha256(rec.get("hr_email")),
                mask_phone(rec.get("hr_phone")),
                rec.get("posted_date"),
                source
            ))

    conn = psycopg2.connect(
        host=os.getenv("DW_HOST", "postgres"),
        port=5432,
        dbname=os.getenv("DW_DB_NAME", "it_jobs_dw"),
        user=os.getenv("ETL_USER", "airflow_etl_user"),
        password=os.getenv("ETL_PASSWORD", "etl_secret_pass_123")
    )
    try:
        with conn, conn.cursor() as cur:
            if valid_rows:
                # 1. Những nguồn (source) nào vừa được quét trong đợt này sẽ tạm hạ cờ is_active = FALSE
                scanned_sources = list({row[-1] for row in valid_rows})
                cur.execute(
                    "UPDATE silver.clean_jobs SET is_active = FALSE WHERE source = ANY(%s);",
                    (scanned_sources,)
                )

                # 2. Upsert dữ liệu mới: Tin nào còn tồn tại trên nguồn sẽ được bật lại is_active = TRUE và cập nhật last_seen_at
                upsert_sql = """
                    INSERT INTO silver.clean_jobs (
                        job_id, job_title, ilo_group_code, ilo_major_group, base_occupation,
                        company_name, location, experience_level, min_yoe, language_req,
                        work_model, min_salary_vnd, max_salary_vnd, skills,
                        hr_email_sha256, hr_phone_masked, posted_date, source,
                        is_active, last_seen_at
                    ) VALUES %s
                    ON CONFLICT (job_id) DO UPDATE SET
                        job_title = EXCLUDED.job_title,
                        ilo_group_code = EXCLUDED.ilo_group_code,
                        ilo_major_group = EXCLUDED.ilo_major_group,
                        base_occupation = EXCLUDED.base_occupation,
                        experience_level = EXCLUDED.experience_level,
                        min_yoe = EXCLUDED.min_yoe,
                        language_req = EXCLUDED.language_req,
                        work_model = EXCLUDED.work_model,
                        min_salary_vnd = EXCLUDED.min_salary_vnd,
                        max_salary_vnd = EXCLUDED.max_salary_vnd,
                        skills = EXCLUDED.skills,
                        is_active = TRUE,
                        last_seen_at = CURRENT_TIMESTAMP,
                        ingested_at = CURRENT_TIMESTAMP;
                """
                rows_with_status = [row + (True, "NOW()") for row in valid_rows]
                execute_values(cur, upsert_sql, rows_with_status)

            if quarantine_rows:
                execute_values(
                    cur,
                    "INSERT INTO quarantine.rejected_jobs (raw_payload, error_reasons) VALUES %s;",
                    quarantine_rows
                )
        logger.info(f"Hoàn tất Silver: {len(valid_rows)} tin ĐANG TUYỂN (Active) | Đã tự động ẩn các tin bị gỡ khỏi nguồn.")
    finally:
        conn.close()

if __name__ == "__main__":
    process_bronze_to_silver()