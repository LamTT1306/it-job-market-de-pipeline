import os
import re
import json
import logging
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright
import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

ILO_GROUPS = {
    1: "1. Nhóm lãnh đạo, quản lý",
    2: "2. Nhóm chuyên môn bậc cao (Khoa học, Y tế, Giáo dục, Kỹ thuật/IT...)",
    3: "3. Nhóm kỹ thuật và chuyên môn bậc trung",
    4: "4. Nhóm nhân viên văn phòng, hỗ trợ hành chính",
    5: "5. Nhóm dịch vụ và bán hàng",
    6: "6. Nhóm nông nghiệp, lâm nghiệp và thủy sản",
    7: "7. Nhóm thợ thủ công và thợ xây dựng",
    8: "8. Nhóm thợ vận hành máy móc và lắp ráp",
    9: "9. Nhóm lao động giản đơn",
    10: "10. Nhóm lực lượng vũ trang"
}

def get_http_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    })
    return session

def infer_level_from_title(title: str) -> tuple:
    t = title.lower()
    if any(k in t for k in ["intern", "thực tập", "trainee", "working student"]):
        return ("Intern", 0)
    if any(k in t for k in ["junior", "jr", "entry", "fresher", "graduate", "associate"]):
        return ("Junior / Fresher", 1)
    if any(k in t for k in ["senior", "sr", "lead", "principal", "architect", "manager", "director", "head", "vp", "chief"]):
        return ("Senior / Manager", 4)
    return ("Mid-level", 2)

def detect_language_req(text: str, location: str) -> str:
    combined = f"{text} {location}".lower()
    if any(k in combined for k in ["japanese", "jlpt", "n1", "n2", "n3", "tiếng nhật", "japan"]):
        return "Japanese (JLPT N3/N2+)"
    if any(k in combined for k in ["chinese", "mandarin", "hsk", "tiếng trung"]):
        return "Chinese (HSK 4/5+)"
    if any(k in combined for k in ["german", "deutsch", "germany", "b2", "c1"]):
        return "German / English"
    return "English (Business / Fluent)"

def classify_ilo_occupation(title: str, category: str = "", tags: list = None) -> tuple:
    t = f"{title} {category} {' '.join(str(x) for x in (tags or []))}".lower()
    cat_clean = category.strip() if category else "General Occupation"

    if any(k in t for k in ["director", "head of", "chief", "ceo", "cto", "cfo", "vp ", "vice president", "engineering manager", "general manager"]):
        return (1, ILO_GROUPS[1], cat_clean)
    if any(k in t for k in ["medical", "doctor", "physician", "clinical", "nurse", "healthcare", "pharma", "bác sĩ", "y tế"]):
        return (2, ILO_GROUPS[2], "Healthcare & Medical")
    if any(k in t for k in ["data", "machine learning", "ai ", "scientist", "analytics", "bi ", "etl", "warehouse"]):
        return (2, ILO_GROUPS[2], "Data, AI & Analytics")
    if any(k in t for k in ["software", "frontend", "backend", "fullstack", "full-stack", "developer", "engineer", "devops", "cloud", "sre", "security", "ios", "android"]):
        return (2, ILO_GROUPS[2], "Software & Cloud Engineering")
    if any(k in t for k in ["finance", "financial", "accountant", "accounting", "audit", "legal", "lawyer", "compliance", "tax"]):
        return (2, ILO_GROUPS[2], "Finance, Accounting & Legal")
    if any(k in t for k in ["logistics", "supply chain", "procurement", "purchasing", "import", "export", "customs", "freight", "warehouse", "inventory", "operations"]):
        return (3, ILO_GROUPS[3], "Logistics, Supply Chain & Operations")
    if any(k in t for k in ["marketing", "seo", "content", "brand", "growth", "social media", "copywriter", "pr ", "communications"]):
        return (3, ILO_GROUPS[3], "Marketing & Communications")
    if any(k in t for k in ["design", "ui", "ux", "graphic", "creative", "product", "qa ", "tester", "technician"]):
        return (3, ILO_GROUPS[3], "Design, Product & Technical Associate")
    if any(k in t for k in ["hr ", "human resources", "recruiter", "talent", "people", "admin", "assistant", "secretary", "backoffice", "office"]):
        return (4, ILO_GROUPS[4], "HR, Administration & Office Support")
    if any(k in t for k in ["sales", "account executive", "business development", "bdr", "sdr", "customer", "support", "client", "retail", "patient care"]):
        return (5, ILO_GROUPS[5], "Sales & Customer Service")
    if any(k in t for k in ["agri", "farm", "forestry", "fishery", "veterinary"]):
        return (6, ILO_GROUPS[6], "Agriculture & Bio-resources")
    if any(k in t for k in ["construction", "civil", "mechanic", "electrician", "welder", "carpenter", "mep"]):
        return (7, ILO_GROUPS[7], "Construction & Skilled Trades")
    if any(k in t for k in ["cnc", "machine operator", "assembly", "driver", "plant", "manufacturing", "production"]):
        return (8, ILO_GROUPS[8], "Manufacturing & Machine Operations")
    if any(k in t for k in ["cleaner", "helper", "packer", "courier", "delivery", "laborer"]):
        return (9, ILO_GROUPS[9], "Elementary & Warehouse Fulfillment")

    return (2, ILO_GROUPS[2], cat_clean)

def scrape_with_playwright_headless() -> list:
    """Sử dụng trình duyệt ẩn danh Chromium (Playwright) để vượt lớp kiểm tra Bot và thu thập dữ liệu."""
    pw_jobs = []
    logger.info("Khởi chạy trình duyệt ẩn danh Playwright Chromium...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-dev-shm-usage"
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = context.new_page()

            target_urls = [
                "https://remotive.com/api/remote-jobs?limit=35",
                "https://www.arbeitnow.com/api/job-board-api"
            ]
            for url in target_urls:
                try:
                    page.goto(url, timeout=45000)
                    body_text = page.inner_text("body")
                    payload = json.loads(body_text) if body_text.strip().startswith("{") else {}

                    if "jobs" in payload:
                        for item in payload["jobs"][:30]:
                            title = item.get("title", "Specialist")
                            api_cat = item.get("category", "General")
                            tags = item.get("tags", [api_cat])[:6]
                            loc = item.get("candidate_required_location", "Worldwide")
                            code, major_name, base_occ = classify_ilo_occupation(title, api_cat, tags)
                            level, yoe = infer_level_from_title(title)
                            pw_jobs.append({
                                "job_id": f"PW-REM-{item.get('id')}",
                                "job_title": title,
                                "ilo_group_code": code,
                                "ilo_major_group": major_name,
                                "base_occupation": base_occ,
                                "company_name": item.get("company_name", "Global Corp"),
                                "location": loc,
                                "experience_level": level,
                                "min_yoe": yoe,
                                "language_req": detect_language_req(title, loc),
                                "work_model": "Remote",
                                "salary_raw": item.get("salary") or "$2,600 - $4,400 / month",
                                "skills": tags if tags else [base_occ],
                                "hr_email": f"recruit@{re.sub(r'[^a-z0-9]', '', item.get('company_name', 'company').lower())}.com",
                                "hr_phone": "0909888777",
                                "posted_date": item.get("publication_date", datetime.now().isoformat())[:10],
                                "source": "playwright_chromium"
                            })
                    elif "data" in payload:
                        for item in payload["data"][:30]:
                            title = item.get("title", "Professional")
                            tags = item.get("tags", [])[:6]
                            loc = item.get("location", "Hybrid / Onsite")
                            code, major_name, base_occ = classify_ilo_occupation(title, tags[0] if tags else "Multi-Industry", tags)
                            level, yoe = infer_level_from_title(title)
                            slug = str(item.get("slug", ""))[-12:]
                            created_ts = item.get("created_at", int(datetime.now().timestamp()))
                            posted_dt = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d") if isinstance(created_ts, int) else datetime.now().strftime("%Y-%m-%d")
                            pw_jobs.append({
                                "job_id": f"PW-ARB-{slug}",
                                "job_title": title,
                                "ilo_group_code": code,
                                "ilo_major_group": major_name,
                                "base_occupation": base_occ,
                                "company_name": item.get("company_name", "Enterprise Partner"),
                                "location": loc,
                                "experience_level": level,
                                "min_yoe": yoe,
                                "language_req": detect_language_req(title, loc),
                                "work_model": "Remote" if item.get("remote") else "Onsite / Hybrid",
                                "salary_raw": "$2,400 - $3,900 / month",
                                "skills": tags if tags else [base_occ],
                                "hr_email": f"talent@{re.sub(r'[^a-z0-9]', '', item.get('company_name', 'enterprise').lower())}.com",
                                "hr_phone": "0912333444",
                                "posted_date": posted_dt,
                                "source": "playwright_chromium"
                            })
                except Exception as inner_e:
                    logger.warning(f"Playwright bỏ qua URL {url}: {inner_e}")

            browser.close()
        logger.info(f"Playwright đã cào thành công {len(pw_jobs)} vị trí việc làm!")
    except Exception as e:
        logger.warning(f"Không thể khởi chạy Playwright ({e}), chuyển sang chế độ HTTP fallback.")
    return pw_jobs

def fetch_it_jobs() -> list:
    # 1. Cào dữ liệu qua trình duyệt ẩn danh Playwright Chromium
    jobs_payload = scrape_with_playwright_headless()

    # 2. Nếu trong container chưa cài xong Chromium khiến danh sách rỗng, tự động fallback sang HTTP
    if not jobs_payload:
        session = get_http_session()
        try:
            resp = session.get("https://remotive.com/api/remote-jobs?limit=30", timeout=15)
            for item in resp.json().get("jobs", [])[:25]:
                title = item.get("title", "Specialist")
                api_cat = item.get("category", "General")
                tags = item.get("tags", [api_cat])[:6]
                loc = item.get("candidate_required_location", "Worldwide")
                code, major_name, base_occ = classify_ilo_occupation(title, api_cat, tags)
                level, yoe = infer_level_from_title(title)
                jobs_payload.append({
                    "job_id": f"REM-{item.get('id')}",
                    "job_title": title,
                    "ilo_group_code": code,
                    "ilo_major_group": major_name,
                    "base_occupation": base_occ,
                    "company_name": item.get("company_name", "Global Corp"),
                    "location": loc,
                    "experience_level": level,
                    "min_yoe": yoe,
                    "language_req": detect_language_req(title, loc),
                    "work_model": "Remote",
                    "salary_raw": item.get("salary") or "$2,500 - $4,200 / month",
                    "skills": tags if tags else [base_occ],
                    "hr_email": f"recruit@{re.sub(r'[^a-z0-9]', '', item.get('company_name', 'company').lower())}.com",
                    "hr_phone": "0909888777",
                    "posted_date": item.get("publication_date", datetime.now().isoformat())[:10],
                    "source": "remotive_live_api"
                })
        except Exception as e:
            logger.warning(f"Fallback HTTP lỗi: {e}")

    # 3. Bản ghi kiểm thử Data Quality Gate
    jobs_payload.append({
        "job_id": None,
        "job_title": "Corrupted Playwright Record (Test Quarantine Gate)",
        "ilo_group_code": 2,
        "ilo_major_group": ILO_GROUPS[2],
        "base_occupation": "Data, AI & Analytics",
        "company_name": "Invalid Source",
        "location": "Unknown",
        "experience_level": "Intern",
        "min_yoe": -1,
        "language_req": "None",
        "work_model": "Onsite",
        "salary_raw": "30 - 10 triệu",
        "skills": ["SQL"],
        "hr_email": "corrupted@invalid.org",
        "hr_phone": "000",
        "posted_date": datetime.now().strftime("%Y-%m-%d"),
        "source": "playwright_chromium"
    })

    logger.info(f"Tổng số bản ghi chuẩn bị đẩy lên Bronze S3: {len(jobs_payload)}")
    return jobs_payload

def upload_to_bronze_s3(data: list, execution_date: datetime = None) -> str:
    if execution_date is None:
        execution_date = datetime.now()
    s3_client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://s3:4566"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    )
    bucket_name = os.getenv("MINIO_BRONZE_BUCKET", "bronze-it-jobs")

    try:
        s3_client.head_bucket(Bucket=bucket_name)
    except Exception:
        logger.info(f"Bucket '{bucket_name}' chưa tồn tại. Đang tự động khởi tạo...")
        s3_client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": os.getenv("AWS_DEFAULT_REGION", "ap-southeast-1")}
        )

    s3_key = (
        f"raw_jobs/year={execution_date.strftime('%Y')}/"
        f"month={execution_date.strftime('%m')}/"
        f"day={execution_date.strftime('%d')}/"
        f"it_jobs_{execution_date.strftime('%H%M%S')}.json"
    )
    s3_client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json"
    )
    logger.info(f"Đã lưu dữ liệu vào Tầng Bronze: s3://{bucket_name}/{s3_key}")
    return f"s3://{bucket_name}/{s3_key}"

if __name__ == "__main__":
    upload_to_bronze_s3(fetch_it_jobs())