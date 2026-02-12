# -*- coding: utf-8 -*-
"""
분양가 산정 Tool (Streamlit) - v14.12(A5)
- A안(Leaflet/OSM 기반) 유지 + 클릭 시 핀/좌표 갱신
- 법정동코드 자동추적(지도 클릭) 안정화/가속
- 기준 계약년월/최근기간: "직접입력" -> "선택형"으로 복구(약속 유지)
- 실거래 조회: 공공데이터 RTMS 호출 오류(HTTPError/403 등) 메시지 개선 + 키 인코딩 자동대응
※ 기존 기능(실거래/법정동/자동추적 흐름) 삭제/변경 없이, 오류/UX만 보강
"""

import hashlib
import json
import os
import urllib.parse
from datetime import datetime
from dateutil.relativedelta import relativedelta

import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
import folium


# ----------------------------
# 기본 설정
# ----------------------------
st.set_page_config(page_title="분양가 산정 Tool - 안정형", layout="wide")

APP_VERSION = "v14.12(A5)"
DEFAULT_LAT = 37.5665
DEFAULT_LON = 126.9780
DEFAULT_ZOOM = 13

# RTMS(국토부 실거래가) - 아파트 매매 (기존 사용 URL 유지)
APT_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


def _get_secret(name: str, default: str = "") -> str:
    """Streamlit secrets / env 모두 지원"""
    try:
        v = st.secrets.get(name, default)
    except Exception:
        v = default
    v = os.getenv(name, v)
    return (v or "").strip()


SERVICE_KEY_RAW = _get_secret("SERVICE_KEY")
KAKAO_REST_API_KEY = _get_secret("KAKAO_REST_API_KEY")


# ----------------------------
# 유틸: 날짜/기간
# ----------------------------
def ym_list(end_ym: str, months_back: int) -> list[str]:
    """end_ym(YYYYMM) 기준으로 months_back개월치 YYYYMM 리스트(내림차순)"""
    dt = datetime.strptime(end_ym + "01", "%Y%m%d")
    out = []
    for i in range(months_back):
        d = dt - relativedelta(months=i)
        out.append(d.strftime("%Y%m"))
    return out


# ----------------------------
# 카카오: 좌표->법정동 코드 (cache)
# ----------------------------
@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def kakao_coord2region(lat: float, lon: float, rest_api_key: str) -> dict:
    """
    Kakao Local API: 좌표->행정구역 코드
    docs: /v2/local/geo/coord2regioncode.json
    """
    if not rest_api_key:
        return {"ok": False, "error": "KAKAO_REST_API_KEY 미설정"}

    url = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
    headers = {"Authorization": f"KakaoAK {rest_api_key}"}
    params = {"x": lon, "y": lat}

    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "body": r.text[:2000]}

    data = r.json()
    docs = data.get("documents") or []
    # region_type=B(법정동) 우선
    b = next((d for d in docs if d.get("region_type") == "B"), None) or (docs[0] if docs else None)
    if not b:
        return {"ok": False, "error": "documents 비어있음", "raw": data}

    # 법정동코드: b_code(10자리)
    b_code = b.get("code", "")  # 10자리
    name = " ".join([x for x in [b.get("region_1depth_name"), b.get("region_2depth_name"), b.get("region_3depth_name")] if x])
    return {"ok": True, "lawd10": b_code, "name": name, "region_type": b.get("region_type")}


# ----------------------------
# RTMS(실거래) 호출
# ----------------------------
def _service_key_candidates(raw: str) -> list[str]:
    """
    ServiceKey가
    - 이미 URL-encoded(…%2B…%3D) 형태로 저장되어 있을 수도 있고
    - decode 된 형태(…+/=…)로 저장되어 있을 수도 있음.
    -> 둘 다 시도해서 403/400 줄이기
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    cands = []

    # 1) 원본 그대로 (많은 경우 이게 정답)
    cands.append(raw)

    # 2) URL decode 버전(원본이 % 인코딩인 경우)
    try:
        dec = urllib.parse.unquote(raw)
        if dec and dec != raw:
            cands.append(dec)
    except Exception:
        pass

    # 3) URL encode 버전(원본이 디코딩키인 경우)
    try:
        enc = urllib.parse.quote(raw, safe="")
        if enc and enc != raw:
            cands.append(enc)
    except Exception:
        pass

    # 중복 제거(순서 유지)
    seen = set()
    uniq = []
    for k in cands:
        if k not in seen:
            uniq.append(k)
            seen.add(k)
    return uniq


def fetch_rtms(url: str, lawd5: str, ym: str, service_key_raw: str) -> tuple[pd.DataFrame | None, dict]:
    """
    RTMS 호출 결과:
    - (df, meta)
    - 실패 시 df=None, meta에 error/diagnostic 포함
    """
    cands = _service_key_candidates(service_key_raw)
    if not cands:
        return None, {"ok": False, "error": "SERVICE_KEY 미설정"}

    # RTMS는 보통 ServiceKey를 querystring으로 넣는 방식이 가장 안정적.
    # (requests params에 넣으면 인코딩이 꼬이는 경우가 있어, 여기선 URL에 직접 붙여서 시도)
    base_params = {
        "LAWD_CD": lawd5,
        "DEAL_YMD": ym,
        "pageNo": 1,
        "numOfRows": 1000,
        "_type": "json",
    }

    last_meta = None

    for key in cands:
        try:
            params = dict(base_params)
            # URL에 ServiceKey를 직접 주입
            qs = urllib.parse.urlencode({**params, "serviceKey": key}, doseq=True)
            full_url = f"{url}?{qs}"

            r = requests.get(full_url, timeout=20)
            txt = r.text

            if r.status_code != 200:
                last_meta = {
                    "ok": False,
                    "http": r.status_code,
                    "error": f"HTTP {r.status_code}",
                    "key_variant": "raw/dec/enc",
                    "body_head": txt[:400],
                }
                continue

            # JSON parse
            try:
                data = r.json()
            except Exception:
                last_meta = {"ok": False, "http": 200, "error": "JSON 파싱 실패", "body_head": txt[:400]}
                continue

            body = (data.get("response") or {}).get("body") or {}
            header = (data.get("response") or {}).get("header") or {}
            result_code = header.get("resultCode")
            result_msg = header.get("resultMsg")

            if str(result_code) != "00":
                last_meta = {"ok": False, "http": 200, "error": f"resultCode={result_code} resultMsg={result_msg}", "raw": header}
                continue

            items = (((body.get("items") or {}).get("item")) or [])
            if isinstance(items, dict):
                items = [items]

            df = pd.DataFrame(items)
            return df, {"ok": True, "count": len(df), "resultMsg": result_msg, "ym": ym}

        except requests.RequestException as e:
            last_meta = {"ok": False, "error": f"요청 실패: {e.__class__.__name__}", "detail": str(e)}
            continue

    return None, (last_meta or {"ok": False, "error": "알 수 없는 오류"})


@st.cache_data(show_spinner=False, ttl=60 * 60)  # 1시간
def cached_fetch_rtms(url: str, lawd5: str, ym: str, key_fingerprint: str) -> tuple[pd.DataFrame | None, dict]:
    # key_fingerprint는 캐시 분리용(키 변경 시 갱신)
    return fetch_rtms(url, lawd5, ym, SERVICE_KEY_RAW)


# ----------------------------
# Session init
# ----------------------------
if "lat" not in st.session_state:
    st.session_state.lat = DEFAULT_LAT
if "lon" not in st.session_state:
    st.session_state.lon = DEFAULT_LON
if "zoom" not in st.session_state:
    st.session_state.zoom = DEFAULT_ZOOM
if "lawd10" not in st.session_state:
    st.session_state.lawd10 = ""
if "region_name" not in st.session_state:
    st.session_state.region_name = ""
if "last_click_ts" not in st.session_state:
    st.session_state.last_click_ts = 0


# ----------------------------
# Sidebar (UI: 선택형 복구)
# ----------------------------
with st.sidebar:
    st.header("설정")

    product = st.selectbox("상품", ["아파트+오피스텔"], index=0)

    # ✅ 약속대로: 직접입력(YYYYMM) → 선택형(연/월)
    now = datetime.now()
    year_candidates = list(range(now.year - 3, now.year + 2))
    default_year = st.session_state.get("end_year", now.year)
    if default_year not in year_candidates:
        default_year = now.year
    end_year = st.selectbox("기준 계약년도", year_candidates, index=year_candidates.index(default_year))
    st.session_state.end_year = end_year

    month_candidates = list(range(1, 13))
    default_month = st.session_state.get("end_month", now.month)
    if default_month not in month_candidates:
        default_month = now.month
    end_month = st.selectbox("기준 계약월", month_candidates, index=month_candidates.index(default_month))
    st.session_state.end_month = end_month

    end_ym = f"{end_year}{end_month:02d}"

    # ✅ 약속대로: 최근기간도 선택형 유지
    recent_options = [1, 3, 6, 12, 24]
    recent_labels = {1: "최근 1개월", 3: "최근 3개월", 6: "최근 6개월", 12: "최근 12개월", 24: "최근 24개월"}
    default_recent = st.session_state.get("months_back", 6)
    if default_recent not in recent_options:
        default_recent = 6
    months_back = st.selectbox(
        "최근기간",
        recent_options,
        format_func=lambda x: recent_labels.get(x, f"{x}개월"),
        index=recent_options.index(default_recent),
    )
    st.session_state.months_back = months_back

    st.divider()

    auto_lawd = st.toggle("법정동코드 자동 추적(지도 클릭)", value=True)

    provider = st.selectbox("자동추적 제공자", ["auto", "kakao"], index=0)

    debug = st.toggle("디버그(오류 상세 보기)", value=False)

    st.button("연결 테스트(서울시청)", use_container_width=True)

st.title("분양가 산정 Tool - 안정형")
st.caption(f"버전: {APP_VERSION} | 지도: Leaflet/OSM(+Kakao 지역코드) | 실거래: 공공데이터(RTMS)")


# ----------------------------
# Layout
# ----------------------------
left, right = st.columns([1.6, 1.0], gap="large")

with left:
    st.subheader("지도")
    st.write("지도 클릭 시 **핀/좌표가 즉시 갱신**되며, 자동추적이 켜져 있으면 **법정동코드/지역명도 자동 표시**됩니다.")

    # Folium map
    m = folium.Map(location=[st.session_state.lat, st.session_state.lon], zoom_start=st.session_state.zoom, control_scale=True)
    folium.Marker([st.session_state.lat, st.session_state.lon], tooltip="선택 위치").add_to(m)

    out = st_folium(m, height=520, width="100%")

    # 클릭 처리(속도/안정성)
    clicked = (out or {}).get("last_clicked")
    if clicked:
        lat = float(clicked.get("lat"))
        lon = float(clicked.get("lng"))

        # 클릭 시마다 갱신(동일 좌표라도 클릭 이벤트로 보고 갱신)
        st.session_state.lat = lat
        st.session_state.lon = lon
        st.session_state.last_click_ts = int(datetime.now().timestamp())

        # 자동추적(카카오)
        if auto_lawd and provider in ("auto", "kakao"):
            info = kakao_coord2region(lat, lon, KAKAO_REST_API_KEY)
            if info.get("ok"):
                st.session_state.lawd10 = info.get("lawd10", "") or ""
                st.session_state.region_name = info.get("name", "") or ""
            else:
                # 실패해도 기존 값은 유지 (기능 삭제/초기화 X)
                if debug:
                    st.warning(f"자동추적 실패: {info.get('error')}")
                    if info.get("body"):
                        st.code(info.get("body")[:800])
        # 클릭 후 화면 반영
        st.rerun()

    st.markdown(f"**핀 좌표:** `{st.session_state.lat:.10f}`, `{st.session_state.lon:.10f}`")


with right:
    st.subheader("선택 위치(법정동)")

    # 법정동코드 입력 + 지역명 표시(옆에 표시)
    c1, c2 = st.columns([1.0, 1.0])
    with c1:
        lawd10 = st.text_input("법정동코드(10자리)", value=st.session_state.lawd10, placeholder="예) 1114010300")
    with c2:
        st.text_input("지역명(표시)", value=st.session_state.region_name, disabled=True, placeholder="지도 클릭 시 자동 표시")

    # 사용자가 수동으로 입력한 경우 session 반영(지역명은 기존값 유지)
    if lawd10 != st.session_state.lawd10:
        st.session_state.lawd10 = lawd10.strip()

    lawd10_clean = (st.session_state.lawd10 or "").strip()
    lawd5 = lawd10_clean[:5] if len(lawd10_clean) >= 5 else ""

    st.caption(f"자동추적 상태: {'ON' if auto_lawd else 'OFF'} | 제공자: {provider}")

    st.divider()
    st.subheader("실거래 조회")

    st.caption(f"기준 계약년월: **{end_ym}** | 최근기간: **{months_back}개월**")
    run = st.button("실거래 조회", type="primary", use_container_width=True)

    if run:
        if not lawd5 or len(lawd5) != 5 or not lawd5.isdigit():
            st.error("법정동코드(최소 5자리)가 필요합니다. (지도 클릭 또는 입력)")
        elif not SERVICE_KEY_RAW:
            st.error("SERVICE_KEY가 설정되어 있지 않습니다. (Streamlit secrets 확인)")
        else:
            key_fp = hashlib.sha256(SERVICE_KEY_RAW.encode("utf-8")).hexdigest()[:12]
            yms = ym_list(end_ym, months_back)

            dfs = []
            metas = []

            with st.spinner("실거래 데이터를 조회 중입니다..."):
                for ym in yms:
                    df, meta = cached_fetch_rtms(APT_URL, lawd5, ym, key_fp)
                    metas.append(meta)
                    if df is not None and not df.empty:
                        df["DEAL_YMD"] = ym
                        dfs.append(df)

            # 에러 요약
            ok_count = sum(1 for m in metas if m.get("ok"))
            fail = [m for m in metas if not m.get("ok")]

            if ok_count == 0 and fail:
                # 실패만 있는 경우: 가장 마지막 실패 원인 표시
                m = fail[-1]
                st.error(f"실거래 조회 실패: {m.get('error')}")
                if debug:
                    st.write(m)
                st.info("힌트: (1) 서비스키(인코딩/디코딩) 문제, (2) 공공데이터 일시 장애, (3) 요청량/차단 가능성")
            else:
                st.success(f"조회 완료: {ok_count}/{len(yms)}개월 응답 OK")

            if dfs:
                all_df = pd.concat(dfs, ignore_index=True)

                # 화면에 최소한의 컬럼만 보여주되, 원본 df는 유지
                preferred_cols = [
                    "DEAL_YMD", "aptNm", "excluUseAr", "dealAmount", "floor", "umdNm", "jibun", "buildYear", "roadNm"
                ]
                cols = [c for c in preferred_cols if c in all_df.columns]
                if cols:
                    st.dataframe(all_df[cols], use_container_width=True, height=360)
                else:
                    st.dataframe(all_df, use_container_width=True, height=360)

                # 다운로드(기존 흐름 유지)
                csv = all_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button("CSV 다운로드", data=csv, file_name=f"rtms_{lawd5}_{end_ym}_{months_back}m.csv", mime="text/csv")
            else:
                st.warning("조회 결과가 비어 있습니다(해당 기간 거래 없음 또는 필터 조건).")

