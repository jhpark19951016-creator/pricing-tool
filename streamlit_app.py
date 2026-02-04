st.write("✅ BUILD: 2026-02-04 auto/10digit/weights")
# -*- coding: utf-8 -*-
"""
분양가 산정 Tool (최종 안정 버전)
- 지도(클릭)로 대상지 좌표 선택
- 좌표 -> 시군구(법정동코드 5자리) 자동 추정 (Nominatim reverse geocode 기반)
- 국토부 실거래(아파트/오피스텔) API 조회 -> 표로 출력
- 결과가 없으면 "없음" 표 출력 (OK 로그 루프 제거)
- 보고서(PDF) 생성: 좌표/행정구역/조회조건/요약통계/상위 거래 리스트
※ 안정성을 위해 "단지명 -> 좌표(geocode_complex_name)" 기능은 제거했습니다.
"""

import os
import re
import io
import math
import datetime as dt
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

# 지도
import folium
from streamlit_folium import st_folium

# XML 파싱(외부 패키지 xmltodict 미사용: 안정성)
import xml.etree.ElementTree as ET

# PDF(없을 수도 있으니 try)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False


# -----------------------------
# 유틸
# -----------------------------
def safe_int(x, default=None):
    try:
        return int(str(x).strip())
    except Exception:
        return default


def safe_float(x, default=None):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default


def ym_iter(end_ym: str, months_back: int) -> List[str]:
    """end_ym(YYYYMM) 기준으로 과거 months_back개월까지 YYYYMM 리스트 반환 (최신->과거)"""
    end_dt = dt.datetime.strptime(end_ym + "01", "%Y%m%d")
    out = []
    for i in range(months_back):
        d = end_dt - dt.timedelta(days=30*i)
        out.append(d.strftime("%Y%m"))
    # 중복 제거/정렬(최신->과거)
    out = sorted(list(set(out)), reverse=True)
    return out


def parse_korean_money_to_million_won(deal_amount: str) -> Optional[int]:
    """
    국토부 실거래 API의 dealAmount는 보통 "84,500" 같은 '만원' 단위 문자열.
    -> int(만원)
    """
    if deal_amount is None:
        return None
    s = str(deal_amount).strip().replace(",", "")
    return safe_int(s, default=None)


def et_get_text(node: Optional[ET.Element], tag: str) -> Optional[str]:
    if node is None:
        return None
    child = node.find(tag)
    if child is None:
        return None
    return child.text


def api_get_xml(url: str, params: Dict[str, Any], timeout: int = 20) -> Tuple[int, str]:
    """GET 요청 -> (status_code, text)"""
    r = requests.get(url, params=params, timeout=timeout)
    return r.status_code, r.text


def parse_rtms_response(xml_text: str) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """
    RTMS XML 응답 파싱
    return: (header_dict, items_list)
    """
    header = {"resultCode": "NA", "resultMsg": "NA"}
    items: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return header, items

    # header
    header_node = root.find(".//header")
    if header_node is not None:
        rc = et_get_text(header_node, "resultCode")
        rm = et_get_text(header_node, "resultMsg")
        if rc:
            header["resultCode"] = rc
        if rm:
            header["resultMsg"] = rm

    # items
    item_nodes = root.findall(".//item")
    for it in item_nodes:
        row = {}
        for child in list(it):
            row[child.tag] = (child.text or "").strip()
        items.append(row)
    return header, items


def normalize_items(items: List[Dict[str, Any]], product: str) -> pd.DataFrame:
    """
    아파트/오피스텔 item의 필드명이 조금 다를 수 있어 공통 컬럼으로 정규화
    컬럼: 단지명, 전용면적(㎡), 거래금액(만원), 거래일, 층, 도로명/지번 등
    """
    rows = []
    for it in items:
        # 공통 후보 키들
        nm = it.get("aptNm") or it.get("offiNm") or it.get("complexName") or it.get("name") or it.get("단지명")
        area = it.get("excluUseAr") or it.get("excluUseArea") or it.get("area") or it.get("전용면적")
        amount = it.get("dealAmount") or it.get("dealAmount") or it.get("거래금액")
        floor = it.get("floor") or it.get("floorCnt") or it.get("층")
        # 날짜: dealYear/dealMonth/dealDay (혹은 계약년월/일)
        y = it.get("dealYear") or it.get("year")
        m = it.get("dealMonth") or it.get("month")
        d = it.get("dealDay") or it.get("day")
        deal_ymd = None
        if y and m and d:
            try:
                deal_ymd = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            except Exception:
                deal_ymd = None

        # 보조정보
        jibun = it.get("jibun") or it.get("지번")
        road = it.get("roadNm") or it.get("roadName") or it.get("도로명")
        dong = it.get("umdNm") or it.get("법정동")
        build_year = it.get("buildYear") or it.get("건축년도")

        rows.append({
            "상품": product,
            "단지명": nm or "",
            "전용면적(㎡)": safe_float(area, default=None),
            "거래금액(만원)": parse_korean_money_to_million_won(amount),
            "거래일": deal_ymd,
            "층": safe_int(floor, default=None),
            "법정동": dong or "",
            "도로명": road or "",
            "지번": jibun or "",
            "건축년도": safe_int(build_year, default=None),
        })

    df = pd.DataFrame(rows)
    # 거래일 파싱/정렬용
    if "거래일" in df.columns:
        df["거래일_dt"] = pd.to_datetime(df["거래일"], errors="coerce")
    else:
        df["거래일_dt"] = pd.NaT
    return df


def hogang_style_filter(df: pd.DataFrame, target_m2: float, tol_m2: float, keyword: str, recent_n: int) -> pd.DataFrame:
    out = df.copy()
    # 면적
    if pd.notna(target_m2) and target_m2 > 0 and pd.notna(tol_m2) and tol_m2 >= 0:
        out = out[(out["전용면적(㎡)"] >= target_m2 - tol_m2) & (out["전용면적(㎡)"] <= target_m2 + tol_m2)]
    # 키워드
    kw = (keyword or "").strip()
    if kw:
        out = out[out["단지명"].astype(str).str.contains(re.escape(kw), na=False)]
    # 최근 N건
    out = out.sort_values("거래일_dt", ascending=False)
    if recent_n and recent_n > 0:
        out = out.head(int(recent_n))
    return out


# -----------------------------
# 법정동 코드 로딩/좌표->시군구 추정
# -----------------------------
@st.cache_data(show_spinner=False)
def load_lawd_codes() -> pd.DataFrame:
    """
    같은 폴더의 lawd_codes.xlsx 또는 lawd*.xlsx를 찾아 로드.
    기대 컬럼: code, label (필수)
    """
    candidates = []
    for fn in os.listdir("."):
        lfn = fn.lower()
        if lfn.endswith(".xlsx") and "lawd" in lfn:
            candidates.append(fn)
    # 우선순위
    prefer = ["lawd_codes.xlsx", "lawd_codes_full.xlsx"]
    for p in prefer:
        if p in candidates:
            candidates = [p] + [x for x in candidates if x != p]
            break

    if not candidates:
        # 파일이 없으면 최소한의 빈 DF
        return pd.DataFrame(columns=["code", "label"])

    path = candidates[0]
    try:
        df = pd.read_excel(path)
    except Exception:
        return pd.DataFrame(columns=["code", "label"])

    # 컬럼 정리
    cols = {c.lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("lawd_cd") or cols.get("법정동코드")
    label_col = cols.get("label") or cols.get("법정동명") or cols.get("name")

    if code_col is None or label_col is None:
        # 사용자가 다른 형식 올렸을 때
        # 첫 두 컬럼을 가정
        if df.shape[1] >= 2:
            code_col = df.columns[0]
            label_col = df.columns[1]
        else:
            return pd.DataFrame(columns=["code", "label"])

    out = df[[code_col, label_col]].copy()
    out.columns = ["code", "label"]
    out["code"] = out["code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5)
    out["label"] = out["label"].astype(str)
    out = out.drop_duplicates(subset=["code", "label"])
    return out


@st.cache_data(show_spinner=False)
def reverse_geocode_nominatim(lat: float, lon: float) -> Dict[str, Any]:
    """
    Nominatim reverse geocode
    반환 address dict (best-effort)
    """
    url = "https://nominatim.openstreetmap.org/reverse"
    headers = {"User-Agent": "pricing-tool/1.0 (streamlit)"}
    params = {"format": "jsonv2", "lat": lat, "lon": lon, "accept-language": "ko"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
        return data.get("address", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def map_admin_to_lawd(lawd_df: pd.DataFrame, addr: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    reverse geocode address -> lawd label 매칭 -> 시군구 5자리 code 반환
    - '서울특별시 중구' vs '대구광역시 중구' 같은 충돌 방지: 상위행정구역(state) 포함 매칭
    """
    if lawd_df is None or lawd_df.empty or not addr:
        return None, None

    # nominatim address keys는 상황마다 다릅니다.
    # 상위: state(서울특별시/경기도/…)
    # 중간: city/county/borough/city_district/municipality 등
    state = (addr.get("state") or addr.get("province") or addr.get("region") or "").strip()
    # 구/군 후보
    district = (addr.get("city_district") or addr.get("borough") or addr.get("county") or addr.get("city") or addr.get("municipality") or "").strip()

    # 한국어가 아닐 때 대비(영문이면 그대로 매칭 어려움)
    # 그래도 label이 한국어인 경우가 많아서 state/district가 비면 fallback
    if not state and "country" in addr and addr.get("country") != "대한민국":
        return None, None

    # 후보 문자열 만들기
    # 예: "서울특별시 중구"
    cand_full = f"{state} {district}".strip()

    labels = lawd_df["label"].astype(str)

    hit = None
    if cand_full:
        # 가장 강한 매칭: label이 cand_full로 시작
        hit = lawd_df[labels.str.startswith(cand_full)]
        if hit.empty:
            # 다음: state 포함 + district 포함
            hit = lawd_df[labels.str.contains(re.escape(state), na=False) & labels.str.contains(re.escape(district), na=False)]

    # 마지막 fallback: district만으로(충돌 위험 있으니 결과가 1개일 때만)
    if (hit is None or hit.empty) and district:
        tmp = lawd_df[labels.str.endswith(district)]
        if len(tmp) == 1:
            hit = tmp

    if hit is None or hit.empty:
        return None, None

    # 실거래 API는 시군구 5자리(LAWD_CD)를 사용
    # lawd_df code가 10자리(리까지)일 수 있으므로 앞 5자리 사용
    code = str(hit.iloc[0]["code"])[:5]
    label = str(hit.iloc[0]["label"])
    return code, label


def infer_lawd_from_latlon(lawd_df: pd.DataFrame, lat: float, lon: float) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    좌표 -> reverse geocode -> lawd 매칭
    return: (lawd_cd_5, label, addr_dict)
    """
    addr = reverse_geocode_nominatim(lat, lon)
    lawd_cd, label = map_admin_to_lawd(lawd_df, addr)
    return lawd_cd, label, addr


# -----------------------------
# 실거래 조회
# -----------------------------
def fetch_range_rtms(product: str, base_url: str, service_key: str, lawd_cd_5: str, ym_list: List[str], timeout_sec: int = 20) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """
    ym_list(YYYYMM) 각각 호출해서 합산
    return: (merged_df, debug_list)
    """
    debug = []
    all_items = []
    for ym in ym_list:
        params = {
            "serviceKey": service_key,
            "LAWD_CD": lawd_cd_5,
            "DEAL_YMD": ym,
            "numOfRows": 9999,
            "pageNo": 1,
        }
        try:
            status, text = api_get_xml(base_url, params=params, timeout=timeout_sec)
            header, items = parse_rtms_response(text)
            debug.append({"ym": ym, "http": status, **header, "items": len(items)})
            if header.get("resultCode") == "000":
                all_items.extend(items)
        except Exception as e:
            debug.append({"ym": ym, "http": "ERR", "resultCode": "ERR", "resultMsg": str(e), "items": 0})

    merged = normalize_items(all_items, product=product)
    return merged, debug


def summarize_df(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {"count": 0}
    out = {"count": int(len(df))}
    if df["거래금액(만원)"].notna().any():
        out["avg_price_mw"] = float(df["거래금액(만원)"].dropna().mean())
        out["med_price_mw"] = float(df["거래금액(만원)"].dropna().median())
        out["max_price_mw"] = float(df["거래금액(만원)"].dropna().max())
    else:
        out["avg_price_mw"] = None
        out["med_price_mw"] = None
        out["max_price_mw"] = None
    if df["전용면적(㎡)"].notna().any():
        out["avg_area"] = float(df["전용면적(㎡)"].dropna().mean())
    else:
        out["avg_area"] = None
    return out


def build_pdf_report_bytes(title: str, meta: Dict[str, Any], df: pd.DataFrame) -> bytes:
    """
    reportlab 기반 간단 PDF 생성 (없으면 예외)
    """
    if not REPORTLAB_OK:
        raise RuntimeError("reportlab not available")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    y = h - 20 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, title)
    y -= 10 * mm

    c.setFont("Helvetica", 10)
    lines = [
        f"생성일시: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"좌표: {meta.get('lat')}, {meta.get('lon')}",
        f"추정 시군구: {meta.get('lawd_label')} (LAWD_CD={meta.get('lawd_cd')})",
        f"상품: {meta.get('product')}",
        f"기간: {meta.get('end_ym')} / 최근 {meta.get('months')}개월 (자동확장 최대 60개월 옵션은 앱에서)",
        f"면적필터: {meta.get('target_m2')} ± {meta.get('tol_m2')} ㎡",
        f"키워드: {meta.get('keyword') or '-'} / 최근N: {meta.get('recent_n')}",
    ]
    for ln in lines:
        c.drawString(20 * mm, y, ln)
        y -= 6 * mm

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, y, "요약")
    y -= 8 * mm

    summ = summarize_df(df)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, f"건수: {summ.get('count', 0):,}건")
    y -= 6 * mm
    if summ.get("avg_price_mw") is not None:
        c.drawString(20 * mm, y, f"평균 거래금액(만원): {summ['avg_price_mw']:.1f} / 중앙값: {summ['med_price_mw']:.1f} / 최대: {summ['max_price_mw']:.1f}")
        y -= 6 * mm

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, y, "상위 거래 20건(최근순)")
    y -= 8 * mm

    c.setFont("Helvetica", 8)
    show = df.sort_values("거래일_dt", ascending=False).head(20) if df is not None else pd.DataFrame()
    cols = ["거래일", "단지명", "전용면적(㎡)", "거래금액(만원)", "층"]
    for i, row in show[cols].iterrows():
        if y < 20 * mm:
            c.showPage()
            y = h - 20 * mm
            c.setFont("Helvetica", 8)
        line = f"{row.get('거래일','')} | {str(row.get('단지명',''))[:18]} | {row.get('전용면적(㎡)', '')} | {row.get('거래금액(만원)', '')} | {row.get('층','')}"
        c.drawString(20 * mm, y, line)
        y -= 5 * mm

    c.showPage()
    c.save()
    return buf.getvalue()


# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="분양가 산정 Tool (안정 버전)", layout="wide")

st.title("분양가 산정 Tool — 풀세트 안정 버전")
st.caption("좌표 기반으로 시군구를 추정하고, 아파트/오피스텔 실거래를 조회해 표 + 보고서(PDF)로 제공합니다.")

# Secrets 우선, 없으면 환경변수
SERVICE_KEY = st.secrets.get("SERVICE_KEY", os.environ.get("SERVICE_KEY", "")).strip()

lawd_df = load_lawd_codes()

with st.sidebar:
    st.header("설정")

    product = st.selectbox("상품", ["아파트", "오피스텔", "아파트+오피스텔"], index=0, key="product")

    # API 엔드포인트(혹시 사용자 환경에 맞게 수정 가능)
    st.subheader("실거래 API 엔드포인트")
    apt_url = st.text_input(
        "아파트 매매 실거래 API URL",
        value="https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
        key="apt_url",
    )
    offi_url = st.text_input(
        "오피스텔 매매 실거래 API URL",
        value="https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
        key="offi_url",
    )

    st.subheader("기간/필터")
    end_ym = st.text_input("기준 계약년월(YYYYMM)", value=dt.date.today().strftime("%Y%m"), key="end_ym")
    months = st.number_input("최근 기간(개월)", min_value=1, max_value=60, value=3, step=1, key="months")
    target_m2 = st.number_input("기준 전용면적(㎡)", min_value=0.0, max_value=300.0, value=84.0, step=1.0, key="target_m2")
    tol_m2 = st.number_input("허용오차(±㎡)", min_value=0.0, max_value=50.0, value=10.0, step=1.0, key="tol_m2")
    keyword = st.text_input("단지명 키워드(선택)", value="", key="keyword")
    recent_n = st.number_input("최근 N건 (0=미사용)", min_value=0, max_value=500, value=50, step=10, key="recent_n")

    st.subheader("서비스키")
    if SERVICE_KEY:
        st.success("SERVICE_KEY 로드됨 (Secrets/환경변수)")
    else:
        st.warning("SERVICE_KEY가 없습니다. Streamlit Cloud > App settings > Secrets에 SERVICE_KEY를 넣어주세요.")
        SERVICE_KEY = st.text_input("임시 입력(권장X)", value="", type="password", key="svc_temp").strip()

# 기본 좌표
DEFAULT_LAT = float(st.session_state.get("lat", 37.5665))
DEFAULT_LON = float(st.session_state.get("lon", 126.9780))

left, right = st.columns([1.2, 1])

with left:
    st.subheader("대상지 지도 (클릭으로 핀 이동)")
    show_compare_markers = st.checkbox("비교단지 마커 표시(보고서 생성에 사용)", value=True, key="show_markers")

    # 지도 생성
    m = folium.Map(location=[DEFAULT_LAT, DEFAULT_LON], zoom_start=14, control_scale=True)
    folium.Marker([DEFAULT_LAT, DEFAULT_LON], tooltip="대상지", popup="대상지").add_to(m)

    map_data = st_folium(m, height=430, width=None, returned_objects=["last_clicked"])

    if map_data and map_data.get("last_clicked"):
        lat = map_data["last_clicked"]["lat"]
        lon = map_data["last_clicked"]["lng"]
        st.session_state["lat"] = lat
        st.session_state["lon"] = lon
    else:
        lat = DEFAULT_LAT
        lon = DEFAULT_LON

    st.caption(f"핀 좌표: {lat:.6f}, {lon:.6f}")

    # 좌표 -> 시군구 추정
    if st.button("시군구 자동 추정", key="btn_infer"):
        lawd_cd, lawd_label, addr = infer_lawd_from_latlon(lawd_df, lat, lon)
        st.session_state["lawd_cd"] = lawd_cd
        st.session_state["lawd_label"] = lawd_label
        st.session_state["addr"] = addr

    lawd_cd = st.session_state.get("lawd_cd")
    lawd_label = st.session_state.get("lawd_label")

    if lawd_cd and lawd_label:
        st.success(f"자동 추정 시군구: {lawd_label} (LAWD_CD={lawd_cd})")
    else:
        st.info("시군구 자동 추정을 실행하세요. (좌표 기반)")

    # lawd 목록(미리보기)
    with st.expander("법정동 코드 목록(미리보기)"):
        st.dataframe(lawd_df.head(200), use_container_width=True)

with right:
    st.subheader("주변 시세(매매 실거래) 자동")
    st.caption("아파트/오피스텔 모두 조회(선택) 후 표로 제공합니다. OK 로그만 반복 출력하지 않습니다.")

    run = st.button("실거래 조회 / 재계산", type="primary", key="btn_run")
    if run:
        if not SERVICE_KEY:
            st.error("SERVICE_KEY가 비어 있습니다. Secrets에 등록 후 다시 실행하세요.")
        elif not (lawd_cd and str(lawd_cd).isdigit() and len(str(lawd_cd)) >= 5):
            st.error("시군구(LAWD_CD)가 없습니다. 좌표 선택 후 '시군구 자동 추정'을 먼저 실행하세요.")
        else:
            # 기간 리스트
            end_ym_clean = re.sub(r"[^0-9]", "", str(end_ym))
            if len(end_ym_clean) != 6:
                end_ym_clean = dt.date.today().strftime("%Y%m")
                st.warning(f"기준 계약년월 형식이 올바르지 않아 {end_ym_clean}으로 설정했습니다.")
            ym_list = ym_iter(end_ym_clean, int(months))

            merged_all = []
            debug_all = []

            with st.spinner("실거래 조회 중... (월별 호출 후 합산)"):
                if product in ("아파트", "아파트+오피스텔"):
                    df_apt, dbg_apt = fetch_range_rtms("아파트", apt_url, SERVICE_KEY, str(lawd_cd)[:5], ym_list)
                    merged_all.append(df_apt)
                    debug_all.extend([{"product": "아파트", **d} for d in dbg_apt])

                if product in ("오피스텔", "아파트+오피스텔"):
                    df_offi, dbg_offi = fetch_range_rtms("오피스텔", offi_url, SERVICE_KEY, str(lawd_cd)[:5], ym_list)
                    merged_all.append(df_offi)
                    debug_all.extend([{"product": "오피스텔", **d} for d in dbg_offi])

            merged = pd.concat(merged_all, ignore_index=True) if merged_all else pd.DataFrame()
            # 정리
            merged = merged.dropna(subset=["거래일_dt"], how="all")
            st.session_state["merged_df"] = merged
            st.session_state["debug_calls"] = debug_all
            st.session_state["last_params"] = {
                "lat": lat, "lon": lon,
                "lawd_cd": str(lawd_cd)[:5], "lawd_label": lawd_label,
                "product": product,
                "end_ym": end_ym_clean, "months": int(months),
                "target_m2": float(target_m2), "tol_m2": float(tol_m2),
                "keyword": keyword, "recent_n": int(recent_n),
            }

    # 결과 표시(조회 전에도 안전)
    merged = st.session_state.get("merged_df", pd.DataFrame())
    debug_calls = st.session_state.get("debug_calls", [])

    st.subheader("조회 결과")

    if merged is None or merged.empty:
        st.dataframe(pd.DataFrame([{
            "상태": "조회된 실거래가가 없습니다",
            "안내": "1) 시군구 자동추정 2) 기간(YYYYMM/개월) 3) API 엔드포인트 4) 서비스키 권한을 확인하세요."
        }]), use_container_width=True)
        st.session_state["filtered_df"] = pd.DataFrame()
    else:
        st.caption(f"원본(기간 합산) {len(merged):,}건")
        st.dataframe(
            merged.sort_values(["거래일_dt", "거래금액(만원)"], ascending=[False, False]).head(300)
            .drop(columns=["거래일_dt"], errors="ignore"),
            use_container_width=True
        )

        flt = hogang_style_filter(merged, float(target_m2), float(tol_m2), keyword, int(recent_n))
        st.caption(f"필터 적용 {len(flt):,}건 (전용 {target_m2}±{tol_m2}㎡, 키워드/최근N 적용)")
        if flt.empty:
            st.dataframe(pd.DataFrame([{
                "상태": "필터 조건에서 거래가 없습니다",
                "안내": "허용오차(±㎡)를 늘리거나 키워드를 비우고 다시 조회해보세요."
            }]), use_container_width=True)
        else:
            st.dataframe(
                flt.sort_values(["거래일_dt", "거래금액(만원)"], ascending=[False, False]).head(300)
                .drop(columns=["거래일_dt"], errors="ignore"),
                use_container_width=True
            )
        st.session_state["filtered_df"] = flt

    with st.expander("디버그(월별 호출 결과)"):
        if debug_calls:
            st.dataframe(pd.DataFrame(debug_calls), use_container_width=True)
        else:
            st.info("아직 호출 내역이 없습니다. '실거래 조회/재계산'을 실행하세요.")

    st.divider()
    st.subheader("보고서(PDF) 생성")

    if st.button("보고서 생성", key="btn_report"):
        params = st.session_state.get("last_params")
        flt = st.session_state.get("filtered_df", pd.DataFrame())
        if not params:
            st.error("먼저 '실거래 조회/재계산'을 실행하세요.")
        else:
            try:
                if REPORTLAB_OK:
                    pdf_bytes = build_pdf_report_bytes(
                        title="분양가 산정 Tool — 보고서(요약)",
                        meta=params,
                        df=flt if flt is not None else pd.DataFrame(),
                    )
                    st.download_button(
                        "PDF 다운로드",
                        data=pdf_bytes,
                        file_name="pricing_report.pdf",
                        mime="application/pdf",
                    )
                    st.success("보고서 생성 완료")
                else:
                    # reportlab이 없으면 CSV로라도 제공
                    st.warning("reportlab이 설치되어 있지 않아 PDF를 생성할 수 없습니다. 대신 필터 결과 CSV를 제공합니다.")
                    csv = (flt if flt is not None else pd.DataFrame()).to_csv(index=False).encode("utf-8-sig")
                    st.download_button("CSV 다운로드", data=csv, file_name="filtered_trades.csv", mime="text/csv")
            except Exception as e:
                st.error(f"보고서 생성 실패: {e}")
