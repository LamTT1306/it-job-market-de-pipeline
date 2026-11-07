import os
import json
import logging
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import boto3

# Cấu hình Logging chuẩn Production
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

def get_http_session() -> requests.Session:
    """Tạo HTTP Session có cơ chế Retry & Exponential Backoff khi gặp lỗi 429/5xx."""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=2, # Chờ 2s, 4s, 8s giữa các lần thử lại
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def fetch_it_jobs() -> list:
    """
    Thu thập dữ liệu tuyển dụng IT kết hợp bộ dữ liệu giả lập thực tế 
    (chứa cả dữ liệu chuẩn, thông tin PII nhạy cảm và bản ghi lỗi để test Quality Gate).
    """
    session = get_http_session()
    jobs_payload = []

    # 1. Thử gọi API công khai lấy dữ liệu thực tế (Remotive Public API)
    try:
        logger.info("Đang kết nối API thu thập tin tuyển dụng...")
        resp = session.get("https://remotive.com/api/remote-jobs?category=software-dev&limit=5", timeout=10)
        resp.raise_for_status()
        api_data = resp.json().get("jobs", [])
        for item in api_data[:3]:
            jobs_payload.append({
                "job_id": f"REM-{item.get('id')}",
                "job_title": item.get("title"),
                "company_name": item.get("company_name"),
                "location": item.get("candidate_required_location", "Remote"),
                "salary_raw": item.get("salary") or "15 - 25 triệu",
                "skills": item.get("tags", ["Python", "SQL"]),
                "hr_email": f"hr.contact@{item.get('company_name', 'tech').lower().replace(' ', '')}.com",
                "hr_phone": "0901234567",
                "posted_date": item.get("publication_date", datetime.now().isoformat())[:10],
                "source": "remotive_api"
            })
    except Exception as e:
        logger.warning(f"Bỏ qua nguồn API bên ngoài ({e}), sử dụng dữ liệu nội bộ.")

    # 2. Bổ sung dữ liệu thị trường Việt Nam (Bao gồm PII và Dữ liệu bẩn để test Quarantine)
    vn_market_samples = [
        {
            "job_id": "VN-1001",
            "job_title": "Data Engineer (Junior / Fresher)",
            "company_name": "FPT Software",
            "location": "Hồ Chí Minh",
            "salary_raw": "15 - 25 triệu",
            "skills": ["Python", "SQL", "Apache Airflow", "Docker"],
            "hr_email": "tuyendung.fsoft@fpt.com.vn",
            "hr_phone": "0988111222",
            "posted_date": datetime.now().strftime("%Y-%m-%d"),
            "source": "topcv_vn"
        },
        {
            "job_id": "VN-1002",
            "job_title": "Backend Software Engineer",
            "company_name": "VNG Corporation",
            "location": "Hồ Chí Minh",
            "salary_raw": "25 - 40 triệu",
            "skills": ["Java", "Spring Boot", "PostgreSQL", "Kafka"],
            "hr_email": "talent@vng.com.vn",
            "hr_phone": "0912333444",
            "posted_date": datetime.now().strftime("%Y-%m-%d"),
            "source": "itviec_vn"
        },
        {
            # Bản ghi cố tình bị lỗi (job_id = None, lương max < lương min) để kiểm tra Quarantine ở Tầng Silver
            "job_id": None,
            "job_title": "AI / Data Intern (Bản ghi lỗi)",
            "company_name": "Unknown Tech",
            "location": "Hà Nội",
            "salary_raw": "30 - 10 triệu",
            "skills": ["Python", "PyTorch"],
            "hr_email": "bad_record@unknown.vn",
            "hr_phone": "0900000000",
            "posted_date": datetime.now().strftime("%Y-%m-%d"),
            "source": "topcv_vn"
        }
    ]
    jobs_payload.extend(vn_market_samples)
    logger.info(f"Đã thu thập thành công {len(jobs_payload)} bản ghi.")
    return jobs_payload

def upload_to_bronze_s3(data: list, execution_date: datetime = None) -> str:
    """Đẩy dữ liệu JSON thô lên AWS S3 (LocalStack) theo phân vùng Năm/Tháng/Ngày."""
    if execution_date is None:
        execution_date = datetime.now()

    endpoint_url = os.getenv("S3_ENDPOINT_URL", "http://s3:4566")
    bucket_name = os.getenv("MINIO_BRONZE_BUCKET", "bronze-it-jobs")

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    )

    # Thiết kế đường dẫn chuẩn Hive-style Partitioning
    s3_key = (
        f"raw_jobs/year={execution_date.strftime('%Y')}/"
        f"month={execution_date.strftime('%m')}/"
        f"day={execution_date.strftime('%d')}/"
        f"it_jobs_{execution_date.strftime('%H%M%S')}.json"
    )

    payload_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    s3_client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=payload_bytes,
        ContentType="application/json"
    )

    s3_uri = f"s3://{bucket_name}/{s3_key}"
    logger.info(f"Đã lưu dữ liệu thô vào Tầng Bronze thành công: {s3_uri}")
    return s3_uri

if __name__ == "__main__":
    raw_jobs = fetch_it_jobs()
    upload_to_bronze_s3(raw_jobs)