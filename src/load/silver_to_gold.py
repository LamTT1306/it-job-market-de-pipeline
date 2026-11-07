import os
import logging
import psycopg2
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

def load_silver_to_gold():
    conn = psycopg2.connect(
        host=os.getenv("DW_HOST", "postgres"),
        port=5432,
        dbname=os.getenv("DW_DB_NAME", "it_jobs_dw"),
        user=os.getenv("ETL_USER", "airflow_etl_user"),
        password=os.getenv("ETL_PASSWORD", "etl_secret_pass_123")
    )

    try:
        with conn, conn.cursor() as cur:
            logger.info("Đang cập nhật các bảng Dimension (dim_company, dim_location, dim_date)...")

            # 1. Upsert Dim Company
            cur.execute("""
                INSERT INTO gold.dim_company (company_name)
                SELECT DISTINCT company_name FROM silver.clean_jobs
                ON CONFLICT (company_name) DO NOTHING;
            """)

            # 2. Upsert Dim Location
            cur.execute("""
                INSERT INTO gold.dim_location (location_name)
                SELECT DISTINCT location FROM silver.clean_jobs
                ON CONFLICT (location_name) DO NOTHING;
            """)

            # 3. Upsert Dim Date
            cur.execute("""
                INSERT INTO gold.dim_date (date_id, year, month, day, day_of_week)
                SELECT DISTINCT 
                    posted_date AS date_id,
                    EXTRACT(YEAR FROM posted_date)::INT,
                    EXTRACT(MONTH FROM posted_date)::INT,
                    EXTRACT(DAY FROM posted_date)::INT,
                    TO_CHAR(posted_date, 'Day') AS day_of_week
                FROM silver.clean_jobs
                WHERE posted_date IS NOT NULL
                ON CONFLICT (date_id) DO NOTHING;
            """)

            logger.info("Đang nạp dữ liệu vào bảng Fact (fact_job_postings) với cơ chế Upsert...")

            # 4. Upsert Fact Job Postings kết hợp nối bảng Dim để lấy khóa ngoại ID
            cur.execute("""
                INSERT INTO gold.fact_job_postings (
                    job_id, company_id, location_id, date_id, 
                    min_salary_vnd, max_salary_vnd, avg_salary_vnd, 
                    skills, source
                )
                SELECT 
                    s.job_id,
                    c.company_id,
                    l.location_id,
                    s.posted_date AS date_id,
                    s.min_salary_vnd,
                    s.max_salary_vnd,
                    (s.min_salary_vnd + s.max_salary_vnd) / 2 AS avg_salary_vnd,
                    s.skills,
                    s.source
                FROM silver.clean_jobs s
                JOIN gold.dim_company c ON s.company_name = c.company_name
                JOIN gold.dim_location l ON s.location = l.location_name
                ON CONFLICT (job_id) DO UPDATE SET
                    min_salary_vnd = EXCLUDED.min_salary_vnd,
                    max_salary_vnd = EXCLUDED.max_salary_vnd,
                    avg_salary_vnd = EXCLUDED.avg_salary_vnd,
                    skills = EXCLUDED.skills;
            """)

        logger.info("Hoàn tất nạp dữ liệu thành công vào Tầng Gold (Star Schema).")
    finally:
        conn.close()

if __name__ == "__main__":
    load_silver_to_gold()