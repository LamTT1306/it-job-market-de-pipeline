from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import os

# Thêm đường dẫn src vào python path trong container
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from extract.ingest_to_bronze import upload_to_bronze_s3, fetch_it_jobs
from transform.bronze_to_silver import process_bronze_to_silver
from load.silver_to_gold import load_silver_to_gold

def run_extraction():
    data = fetch_it_jobs()
    upload_to_bronze_s3(data)

default_args = {
    'owner': 'data_engineer_intern',
    'depends_on_past': False,
    'email_on_failure': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='it_jobs_market_etl_pipeline',
    default_args=default_args,
    description='End-to-End Medallion ETL Pipeline cho dữ liệu tuyển dụng IT',
    schedule_interval='@hourly', # Tự động quét dữ liệu mới trên mạng mỗi giờ 1 lần
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['data-engineering', 'medallion', 'etl'],
) as dag:

    t1_extract = PythonOperator(
        task_id='extract_to_bronze_s3',
        python_callable=run_extraction
    )

    t2_transform = PythonOperator(
        task_id='transform_bronze_to_silver',
        python_callable=process_bronze_to_silver
    )

    t3_load = PythonOperator(
        task_id='load_silver_to_gold',
        python_callable=load_silver_to_gold
    )

    # Thiết lập luồng phụ thuộc (DAG Lineage)
    t1_extract >> t2_transform >> t3_load