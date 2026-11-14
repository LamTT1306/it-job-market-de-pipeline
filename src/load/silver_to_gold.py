import os
import logging
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
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
            cur.execute("""
                INSERT INTO gold.dim_company (company_name)
                SELECT DISTINCT company_name FROM silver.clean_jobs
                ON CONFLICT (company_name) DO NOTHING;
            """)
            cur.execute("""
                INSERT INTO gold.dim_location (location_name)
                SELECT DISTINCT location FROM silver.clean_jobs
                ON CONFLICT (location_name) DO NOTHING;
            """)
            cur.execute("""
                INSERT INTO gold.dim_occupation (ilo_group_code, ilo_major_group, base_occupation)
                SELECT DISTINCT ilo_group_code, ilo_major_group, base_occupation FROM silver.clean_jobs
                ON CONFLICT (ilo_group_code, base_occupation) DO UPDATE SET
                    ilo_major_group = EXCLUDED.ilo_major_group;
            """)
            cur.execute("""
                INSERT INTO gold.dim_date (date_id, year, month, day, day_of_week)
                SELECT DISTINCT 
                    posted_date,
                    EXTRACT(YEAR FROM posted_date)::INT,
                    EXTRACT(MONTH FROM posted_date)::INT,
                    EXTRACT(DAY FROM posted_date)::INT,
                    TO_CHAR(posted_date, 'Day')
                FROM silver.clean_jobs
                WHERE posted_date IS NOT NULL
                ON CONFLICT (date_id) DO NOTHING;
            """)

            cur.execute("""
                INSERT INTO gold.fact_job_postings (
                    job_id, job_title, occupation_id, company_id, location_id, date_id,
                    experience_level, min_yoe, language_req, work_model,
                    min_salary_vnd, max_salary_vnd, avg_salary_vnd, source,
                    is_active, last_seen_at
                )
                SELECT 
                    s.job_id, s.job_title, occ.occupation_id, c.company_id, l.location_id, s.posted_date,
                    s.experience_level, s.min_yoe, s.language_req, s.work_model,
                    s.min_salary_vnd, s.max_salary_vnd,
                    (s.min_salary_vnd + s.max_salary_vnd) / 2,
                    s.source,
                    s.is_active,
                    s.last_seen_at
                FROM silver.clean_jobs s
                JOIN gold.dim_occupation occ 
                  ON s.ilo_group_code = occ.ilo_group_code AND s.base_occupation = occ.base_occupation
                JOIN gold.dim_company c ON s.company_name = c.company_name
                JOIN gold.dim_location l ON s.location = l.location_name
                ON CONFLICT (job_id) DO UPDATE SET
                    job_title = EXCLUDED.job_title,
                    occupation_id = EXCLUDED.occupation_id,
                    company_id = EXCLUDED.company_id,
                    location_id = EXCLUDED.location_id,
                    date_id = EXCLUDED.date_id,
                    experience_level = EXCLUDED.experience_level,
                    min_yoe = EXCLUDED.min_yoe,
                    language_req = EXCLUDED.language_req,
                    work_model = EXCLUDED.work_model,
                    min_salary_vnd = EXCLUDED.min_salary_vnd,
                    max_salary_vnd = EXCLUDED.max_salary_vnd,
                    avg_salary_vnd = EXCLUDED.avg_salary_vnd,
                    source = EXCLUDED.source,
                    is_active = EXCLUDED.is_active,
                    last_seen_at = EXCLUDED.last_seen_at;
            """)

            cur.execute("""
                INSERT INTO gold.bridge_job_skills (job_id, skill_name)
                SELECT DISTINCT s.job_id, jsonb_array_elements_text(s.skills) AS skill_name
                FROM silver.clean_jobs s
                WHERE jsonb_typeof(s.skills) = 'array'
                ON CONFLICT (job_id, skill_name) DO NOTHING;
            """)

        logger.info("Hoàn tất cập nhật Star Schema theo chuẩn 10 Nhóm ngành ILO ở Tầng Gold.")
    finally:
        conn.close()

if __name__ == "__main__":
    load_silver_to_gold()
