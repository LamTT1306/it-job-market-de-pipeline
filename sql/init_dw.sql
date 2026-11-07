-- 1. Tạo Database cho Data Warehouse
CREATE DATABASE it_jobs_dw;
\connect it_jobs_dw;

-- 2. Tạo các Schema theo kiến trúc Medallion
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS quarantine;

-- 3. Tạo User cho luồng ETL (Có quyền đọc/ghi trên Silver, Gold, Quarantine)
CREATE USER airflow_etl_user WITH PASSWORD 'etl_secret_pass_123';
GRANT CONNECT ON DATABASE it_jobs_dw TO airflow_etl_user;
GRANT USAGE, CREATE ON SCHEMA silver, gold, quarantine TO airflow_etl_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA silver, gold, quarantine TO airflow_etl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver, gold, quarantine GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO airflow_etl_user;

-- 4. Tạo User cho BI/Analytics (Chỉ có quyền ĐỌC trên tầng Gold)
CREATE USER bi_readonly_user WITH PASSWORD 'bi_readonly_pass_123';
GRANT CONNECT ON DATABASE it_jobs_dw TO bi_readonly_user;
GRANT USAGE ON SCHEMA gold TO bi_readonly_user;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO bi_readonly_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO bi_readonly_user;
ALTER DEFAULT PRIVILEGES FOR ROLE airflow_etl_user IN SCHEMA gold GRANT SELECT ON TABLES TO bi_readonly_user;