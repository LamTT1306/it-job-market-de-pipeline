import os
import unicodedata
import streamlit as st
import pandas as pd
import psycopg2

st.set_page_config(page_title="ILO Global Job Market Data Warehouse", layout="wide")
st.title("🌍 Hệ thống Phân tích Thị trường Lao động Đa ngành (Chuẩn ILO ISCO-08)")
st.caption("Mô hình Phân cấp 3 Tầng: **10 Nhóm ngành chính ILO** -> **Nghề cơ bản** -> **Vị trí chuyên môn** | Truy xuất qua `bi_readonly_user`.")

def remove_accents(text: str) -> str:
    """Chuẩn hóa chuỗi không dấu để tìm kiếm tiếng Việt chính xác 100%."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")

def get_connection():
    return psycopg2.connect(
        host=os.getenv("DW_HOST", "localhost"),
        port=5432,
        dbname=os.getenv("DW_DB_NAME", "it_jobs_dw"),
        user=os.getenv("BI_USER", "bi_readonly_user"),
        password=os.getenv("BI_PASSWORD", "bi_readonly_pass_123")
    )

try:
    only_active = st.sidebar.checkbox("🟢 Chỉ hiển thị tin đang tuyển (Active)", value=True)
    status_clause = "WHERE f.is_active = TRUE" if only_active else ""

    conn = get_connection()
    jobs_query = f"""
        SELECT 
            f.job_id AS "Mã CV",
            occ.ilo_group_code AS "Mã ILO",
            occ.ilo_major_group AS "10 Nhóm ngành chính (ILO)",
            occ.base_occupation AS "Nghề cơ bản",
            f.job_title AS "Vị trí chuyên môn",
            c.company_name AS "Công ty / Tổ chức",
            f.experience_level AS "Cấp bậc",
            f.min_yoe AS "Kinh nghiệm (Năm)",
            f.language_req AS "Yêu cầu Ngoại ngữ",
            f.work_model AS "Hình thức",
            l.location_name AS "Địa điểm",
            ROUND(f.min_salary_vnd / 1000000.0, 1) AS "Lương Min (Tr.VND)",
            ROUND(f.avg_salary_vnd / 1000000.0, 1) AS "Lương TB (Tr.VND)",
            ROUND(f.max_salary_vnd / 1000000.0, 1) AS "Lương Max (Tr.VND)",
            f.source AS "Nguồn",
            f.is_active AS "Đang tuyển"
        FROM gold.fact_job_postings f
        JOIN gold.dim_occupation occ ON f.occupation_id = occ.occupation_id
        JOIN gold.dim_company c ON f.company_id = c.company_id
        JOIN gold.dim_location l ON f.location_id = l.location_id
        {status_clause}
        ORDER BY occ.ilo_group_code ASC, f.avg_salary_vnd DESC;
    """
    skills_query = f"""
        SELECT 
            b.skill_name AS "Kỹ năng / Nghiệp vụ",
            occ.ilo_major_group AS "10 Nhóm ngành chính (ILO)",
            COUNT(b.job_id) AS "Số tin tuyển dụng"
        FROM gold.bridge_job_skills b
        JOIN gold.fact_job_postings f ON b.job_id = f.job_id
        JOIN gold.dim_occupation occ ON f.occupation_id = occ.occupation_id
        {status_clause}
        GROUP BY b.skill_name, occ.ilo_major_group
        ORDER BY COUNT(b.job_id) DESC;
    """
    df = pd.read_sql(jobs_query, conn)
    df_skills = pd.read_sql(skills_query, conn)
    conn.close()

    # BỘ LỌC PHÂN CẤP 3 TẦNG CÓ GỢI Ý TỰ ĐỘNG (AUTOCOMPLETE DROPDOWN)
    f1, f2, f3 = st.columns(3)

    # Tầng 1: 10 Nhóm ngành ILO (Nếu để trống -> Mặc định lấy tất cả)
    with f1:
        ilo_options = sorted(df["10 Nhóm ngành chính (ILO)"].unique().tolist())
        selected_ilo = st.multiselect(
            "1️⃣ Chọn Nhóm ngành chính (ILO) [Để trống = Tất cả]:",
            options=ilo_options,
            placeholder="Gõ hoặc chọn nhóm ngành ILO..."
        )
    df_step1 = df[df["10 Nhóm ngành chính (ILO)"].isin(selected_ilo)] if selected_ilo else df

    # Tầng 2: Nghề cơ bản (Tự động lọc theo Tầng 1, có gợi ý khi gõ)
    with f2:
        occ_options = sorted(df_step1["Nghề cơ bản"].unique().tolist())
        selected_base_occ = st.multiselect(
            "2️⃣ Chọn Nghề cơ bản (~2.000 nghề) [Gõ để gợi ý]:",
            options=occ_options,
            placeholder="Gõ chữ cái bất kỳ để tìm nghề..."
        )
    df_step2 = df_step1[df_step1["Nghề cơ bản"].isin(selected_base_occ)] if selected_base_occ else df_step1

    # Tầng 3: Vị trí chuyên môn (Gõ 1 chữ sẽ hiện ngay danh sách các vị trí khớp để chọn)
    with f3:
        role_options = sorted(df_step2["Vị trí chuyên môn"].unique().tolist())
        selected_roles = st.multiselect(
            "3️⃣ Chọn Vị trí chuyên môn [Gõ 'Bác sĩ', 'Data'... để chọn]:",
            options=role_options,
            placeholder="Gõ 1 chữ bất kỳ để hiện vị trí gợi ý..."
        )
    df_filtered = df_step2[df_step2["Vị trí chuyên môn"].isin(selected_roles)] if selected_roles else df_step2

    # Thanh tìm kiếm bổ trợ không phân biệt dấu Tiếng Việt (Search toàn cục nếu muốn tìm tự do)
    free_kw = st.text_input("🔎 Tìm kiếm nhanh không phân biệt dấu (VD: go 'bac si', 'xuat nhap khau', 'fpt'...):", "")
    if free_kw.strip():
        kw_norm = remove_accents(free_kw.strip())
        mask = (
            df_filtered["Vị trí chuyên môn"].apply(remove_accents).str.contains(kw_norm) |
            df_filtered["Nghề cơ bản"].apply(remove_accents).str.contains(kw_norm) |
            df_filtered["Công ty / Tổ chức"].apply(remove_accents).str.contains(kw_norm)
        )
        df_filtered = df_filtered[mask]

    active_ilo = df_filtered["10 Nhóm ngành chính (ILO)"].unique().tolist()
    df_skills_filtered = df_skills[df_skills["10 Nhóm ngành chính (ILO)"].isin(active_ilo)]

    # 1. KPI Cards (Chống lỗi hiển thị 'nan Triệu' khi không có bản ghi nào)
    c1, c2, c3, c4 = st.columns(4)
    total_jobs = len(df_filtered)
    avg_sal = df_filtered["Lương TB (Tr.VND)"].mean() if total_jobs > 0 else 0.0
    max_sal = df_filtered["Lương Max (Tr.VND)"].max() if total_jobs > 0 else 0.0

    c1.metric("Tổng số vị trí tuyển dụng", f"{total_jobs} vị trí")
    c2.metric("Số Nhóm ILO / Nghề cơ bản", f"{df_filtered['10 Nhóm ngành chính (ILO)'].nunique()} nhóm / {df_filtered['Nghề cơ bản'].nunique()} nghề")
    c3.metric("Lương TB (Nhóm đang chọn)", f"{avg_sal:.1f} Triệu")
    c4.metric("Lương Cao nhất", f"{max_sal:.1f} Triệu")

    st.divider()

    if total_jobs == 0:
        st.warning("Không tìm thấy vị trí nào khớp với bộ lọc hiện tại. Hãy xóa bớt điều kiện lọc ở trên.")
    else:
        # 2. Biểu đồ theo 10 Nhóm ngành ILO & Nghề cơ bản
        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("🏛️ So sánh Lương theo 10 Nhóm ngành chính của ILO")
            ilo_chart = df_filtered.groupby("10 Nhóm ngành chính (ILO)")[["Lương Min (Tr.VND)", "Lương TB (Tr.VND)", "Lương Max (Tr.VND)"]].mean()
            st.bar_chart(ilo_chart, stack=False)

        with col_b:
            st.subheader("🛠️ Lương Trung Bình theo Nhóm Nghề cơ bản")
            occ_chart = df_filtered.groupby("Nghề cơ bản")[["Lương Min (Tr.VND)", "Lương TB (Tr.VND)", "Lương Max (Tr.VND)"]].mean()
            st.bar_chart(occ_chart, stack=False)

        # 3. Biểu đồ Ngoại ngữ & Kỹ năng
        col_c, col_d = st.columns(2)
        with col_c:
            st.subheader("🌐 Lương theo Yêu cầu Ngoại ngữ (Anh / Nhật / Trung / Không)")
            lang_chart = df_filtered.groupby("Yêu cầu Ngoại ngữ")[["Lương Min (Tr.VND)", "Lương TB (Tr.VND)", "Lương Max (Tr.VND)"]].mean()
            st.bar_chart(lang_chart, stack=False)

        with col_d:
            st.subheader("🔥 Top 10 Kỹ năng / Nghiệp vụ xuất hiện nhiều nhất")
            top_skills = df_skills_filtered.groupby("Kỹ năng / Nghiệp vụ")["Số tin tuyển dụng"].sum().sort_values(ascending=False).head(10)
            st.bar_chart(top_skills)

        st.divider()
        st.subheader("📋 Bảng dữ liệu chi tiết Tầng Gold (Chuẩn phân loại ILO)")
        st.dataframe(df_filtered, use_container_width=True)

except Exception as e:
    st.error(f"Lỗi truy vấn: {e}")
