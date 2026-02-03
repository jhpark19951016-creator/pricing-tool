
import streamlit as st
import pandas as pd
import re

st.set_page_config(page_title="분양가 산정 Tool", layout="wide")

@st.cache_data
def load_lawd_codes():
    data = [
        {"code":"11140","label":"서울특별시 중구"},
        {"code":"11200","label":"서울특별시 성동구"},
        {"code":"11500","label":"서울특별시 강서구"},
        {"code":"27110","label":"대구광역시 중구"},
    ]
    return pd.DataFrame(data)

def infer_lawd_from_latlon(lawd_df, lat, lon):
    metro_ko = ""
    dist_ko = ""

    if 37.3 <= lat <= 37.8 and 126.7 <= lon <= 127.2:
        metro_ko = "서울"
    elif 35.7 <= lat <= 36.0 and 128.4 <= lon <= 128.8:
        metro_ko = "대구"

    dist_ko = "중구"

    label_series = lawd_df["label"].astype(str)

    if metro_ko and dist_ko:
        hit = lawd_df[
            label_series.str.contains(re.escape(metro_ko), na=False)
            & label_series.str.contains(re.escape(dist_ko), na=False)
        ]
        if not hit.empty:
            row = hit.iloc[0]
            return row["code"], row["label"]

    hit = lawd_df[label_series.str.contains(re.escape(dist_ko), na=False)]
    if not hit.empty:
        row = hit.iloc[0]
        return row["code"], row["label"]

    return None, None

st.title("분양가 산정 Tool – 풀세트 안정 버전")

lawd_df = load_lawd_codes()

lat = st.number_input("위도", value=37.5665, format="%.6f")
lon = st.number_input("경도", value=126.9780, format="%.6f")

if st.button("시군구 자동 추정"):
    code, label = infer_lawd_from_latlon(lawd_df, lat, lon)
    if code:
        st.success(f"자동 추정 결과: {label} ({code})")
    else:
        st.error("시군구 추정 실패")

st.subheader("법정동 코드 목록")
st.dataframe(lawd_df)
