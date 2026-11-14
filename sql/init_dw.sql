-- 1. Tạo Database cho Data Warehouse
CREATE DATABASE it_jobs_dw;
\connect it_jobs_dw;

-- 2. Tạo các Schema theo kiến trúc Medallion
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS gold;

-- 3. Khởi tạo bảng tầng Silver & Quarantine
CREATE TABLE IF NOT EXISTS silver.clean_jobs (
    job_id VARCHAR(100) PRIMARY KEY,
    job_title VARCHAR(255) NOT NULL,
    ilo_group_code INT NOT NULL,
    ilo_major_group VARCHAR(255) NOT NULL,
    base_occupation VARCHAR(255) NOT NULL,
    company_name VARCHAR(255) NOT NULL,
    location VARCHAR(150),
    experience_level VARCHAR(100),
    min_yoe INT DEFAULT 0,
    language_req VARCHAR(100),
    work_model VARCHAR(50),
    min_salary_vnd BIGINT NOT NULL,
    max_salary_vnd BIGINT NOT NULL,
    skills JSONB DEFAULT '[]'::jsonb,
    hr_email_sha256 CHAR(64),
    hr_phone_masked VARCHAR(30),
    posted_date DATE,
    source VARCHAR(100) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quarantine.rejected_jobs (
    id SERIAL PRIMARY KEY,
    raw_payload JSONB NOT NULL,
    error_reasons TEXT NOT NULL,
    rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Khởi tạo Star Schema tầng Gold (Chuẩn ILO ISCO-08)
CREATE TABLE IF NOT EXISTS gold.dim_occupation (
    occupation_id SERIAL PRIMARY KEY,
    ilo_group_code INT NOT NULL,
    ilo_major_group VARCHAR(255) NOT NULL,
    base_occupation VARCHAR(255) NOT NULL,
    UNIQUE (ilo_group_code, base_occupation)
);

CREATE TABLE IF NOT EXISTS gold.dim_company (
    company_id SERIAL PRIMARY KEY,
    company_name VARCHAR(255) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_location (
    location_id SERIAL PRIMARY KEY,
    location_name VARCHAR(150) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_id DATE PRIMARY KEY,
    year INT NOT NULL,
    month INT NOT NULL,
    day INT NOT NULL,
    day_of_week VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.fact_job_postings (
    job_id VARCHAR(100) PRIMARY KEY,
    job_title VARCHAR(255) NOT NULL,
    occupation_id INT REFERENCES gold.dim_occupation(occupation_id),
    company_id INT REFERENCES gold.dim_company(company_id),
    location_id INT REFERENCES gold.dim_location(location_id),
    date_id DATE REFERENCES gold.dim_date(date_id),
    experience_level VARCHAR(100),
    min_yoe INT DEFAULT 0,
    language_req VARCHAR(100),
    work_model VARCHAR(50),
    min_salary_vnd BIGINT NOT NULL,
    max_salary_vnd BIGINT NOT NULL,
    avg_salary_vnd BIGINT NOT NULL,
    source VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gold.bridge_job_skills (
    job_id VARCHAR(100) REFERENCES gold.fact_job_postings(job_id) ON DELETE CASCADE,
    skill_name VARCHAR(150) NOT NULL,
    PRIMARY KEY (job_id, skill_name)
);

-- 5. Phân quyền RBAC: airflow_etl_user toàn quyền
CREATE USER airflow_etl_user WITH PASSWORD 'etl_secret_pass_123';
GRANT CONNECT ON DATABASE it_jobs_dw TO airflow_etl_user;
GRANT USAGE, CREATE ON SCHEMA silver, quarantine, gold TO airflow_etl_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA silver, quarantine, gold TO airflow_etl_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA silver, quarantine, gold TO airflow_etl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver, quarantine, gold GRANT ALL PRIVILEGES ON TABLES TO airflow_etl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver, quarantine, gold GRANT ALL PRIVILEGES ON SEQUENCES TO airflow_etl_user;

-- 6. Phân quyền RBAC: bi_readonly_user chỉ có quyền SELECT trên gold, REVOKE ALL trên silver & quarantine
CREATE USER bi_readonly_user WITH PASSWORD 'bi_readonly_pass_123';
GRANT CONNECT ON DATABASE it_jobs_dw TO bi_readonly_user;
REVOKE ALL ON SCHEMA silver, quarantine FROM bi_readonly_user;
REVOKE ALL ON ALL TABLES IN SCHEMA silver, quarantine FROM bi_readonly_user;
GRANT USAGE ON SCHEMA gold TO bi_readonly_user;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO bi_readonly_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO bi_readonly_user;
ALTER DEFAULT PRIVILEGES FOR ROLE airflow_etl_user IN SCHEMA gold GRANT SELECT ON TABLES TO bi_readonly_user;
