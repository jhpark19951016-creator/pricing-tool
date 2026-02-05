
# streamlit_app.py
import streamlit as st

st.set_page_config(page_title="분양가 산정 Tool (안정버전)", layout="wide")

st.title("분양가 산정 Tool - 안정버전(v1)")
st.write("이 버전은 안정성 위주 최소 기능 베이스입니다.")

lat = st.number_input("위도", value=37.5665, format="%.6f")
lon = st.number_input("경도", value=126.9780, format="%.6f")

if st.button("좌표 확인"):
    st.success(f"선택 좌표: {lat}, {lon}")

st.info("다음 단계: 법정동코드 선택 → 실거래 조회 (확장 예정)")
