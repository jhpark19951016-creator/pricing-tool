import os
import io
import time
import datetime as dt
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional
import re

import requests
import pandas as pd
import streamlit as st
import xmltodict

import matplotlib.pyplot as plt

import folium
from streamlit_folium import st_folium
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

M2_PER_PYEONG = 3.305785


def ym_backwards(end_ym: str, months: int) -> List[str]:
    y = int(end_ym[:4])
    m = int(end_ym[4:6])
    out = []
    cur = dt.date(y, m, 1)
    for _ in range(max(1, int(months))):
        out.append(cur.strftime("%Y%m"))
        py = cur.year
        pm = cur.month - 1
        if pm == 0:
            pm = 12
            py -= 1
        cur = dt.date(py, pm, 1)
    return out


def to_int(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).strip().replace(",", "")
    if not s:
        return 0
    return int(float(s))


def to_float(x: Any) -> float:
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if not s:
        return 0.0
    return float(s)


def won_per_pyeong_from_trade(deal_amount_manwon: int, exclu_m2: float) -> float:
    if exclu_m2 <= 0:
        return 0.0
    won = deal_amount_manwon * 10000
    won_per_m2 = won / exclu_m2
    return won_per_m2 * M2_PER_PYEONG


def safe_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def fmt0(x: float) -> str:
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


@dataclass
class RtmsConfig:
    service_key: str
    timeout_sec: int = 25
    throttle_sec: float = 0.25


def parse_result_meta(xml_text: str) -> Tuple[str, str]:
    try:
        obj = xmltodict.parse(xml_text)
        header = obj.get("response", {}).get("header", {})
        return str(header.get("resultCode", "")), str(header.get("resultMsg", ""))
    except Exception:
        return "", ""


def fetch_rtms_items(base_url: str, params: Dict[str, str], cfg: RtmsConfig) -> Tuple[List[Dict[str, Any]], str, str, int]:
    """실거래 API 호출 + items를 항상 list로 정규화.

    Returns (items, resultCode, resultMsg, totalCount)
    """
    if cfg.throttle_sec:
        time.sleep(float(cfg.throttle_sec))
    try:
        r = requests.get(base_url, params=params, timeout=float(cfg.timeout_sec))
        r.raise_for_status()
        x = xmltodict.parse(r.text)

        resp = x.get("response", {}) if isinstance(x, dict) else {}
        header = resp.get("header", {}) if isinstance(resp, dict) else {}
        body = resp.get("body", {}) if isinstance(resp, dict) else {}

        result_code = str(header.get("resultCode", "")) if isinstance(header, dict) else ""
        result_msg = str(header.get("resultMsg", "")) if isinstance(header, dict) else ""

        total = 0
        try:
            total = int(body.get("totalCount", 0)) if isinstance(body, dict) else 0
        except Exception:
            total = 0

        items_obj = body.get("items") if isinstance(body, dict) else None
        item = None
        if isinstance(items_obj, dict):
            item = items_obj.get("item")

        items = safe_list(item)

        ok_codes = {"00", "000", "0", ""}  # endpoint 별로 다름
        if result_code not in ok_codes:
            # 오류 코드면 items는 비우고 메타만 반환
            return [], result_code, result_msg, total

        return items, (result_code or "000"), (result_msg or "OK"), total

    except requests.exceptions.Timeout:
        return [], "TIMEOUT", "요청 시간이 초과되었습니다(Timeout)", 0
    except requests.exceptions.RequestException as e:
        return [], "HTTPERR", f"네트워크 오류: {e}", 0
    except Exception as e:
        return [], "PARSEERR", f"응답 파싱 오류: {e}", 0


def get_apartment_trades(cfg: RtmsConfig, lawd_cd: str, deal_ym: str) -> pd.DataFrame:
    base_url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
    params = {"serviceKey": cfg.service_key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ym, "numOfRows": "1000", "pageNo": "1"}
    items, rc, msg, total = fetch_rtms_items(base_url, params, cfg)
    rows = []
    for it in items:
        rows.append({
            "자산": "아파트",
            "단지명": (it.get("aptNm") or "").strip(),
            "전용㎡": to_float(it.get("excluUseAr")),
            "거래금액(만원)": to_int(it.get("dealAmount")),
            "거래년": to_int(it.get("dealYear")),
            "거래월": to_int(it.get("dealMonth")),
            "거래일": to_int(it.get("dealDay")),
            "층": to_int(it.get("floor")),
            "건축년도": to_int(it.get("buildYear")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["거래일자"] = pd.to_datetime(df["거래년"].astype(str) + "-" + df["거래월"].astype(str) + "-" + df["거래일"].astype(str), errors="coerce")
        df["평당가(원/평,전용)"] = df.apply(lambda r: won_per_pyeong_from_trade(int(r["거래금액(만원)"]), float(r["전용㎡"])), axis=1)
    return df


@st.cache_data(ttl=900, show_spinner=False)
def get_officetel_trades(cfg: RtmsConfig, lawd_cd: str, deal_ym: str) -> pd.DataFrame:
    base_url = "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"
    params = {"serviceKey": cfg.service_key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ym, "numOfRows": "1000", "pageNo": "1"}
    items, rc, msg, total = fetch_rtms_items(base_url, params, cfg)
    rows = []
    for it in items:
        rows.append({
            "자산": "오피스텔",
            "단지명": (it.get("offiNm") or it.get("aptNm") or "").strip(),
            "전용㎡": to_float(it.get("excluUseAr")),
            "거래금액(만원)": to_int(it.get("dealAmount")),
            "거래년": to_int(it.get("dealYear")),
            "거래월": to_int(it.get("dealMonth")),
            "거래일": to_int(it.get("dealDay")),
            "층": to_int(it.get("floor")),
            "건축년도": to_int(it.get("buildYear")),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["거래일자"] = pd.to_datetime(df["거래년"].astype(str) + "-" + df["거래월"].astype(str) + "-" + df["거래일"].astype(str), errors="coerce")
        df["평당가(원/평,전용)"] = df.apply(lambda r: won_per_pyeong_from_trade(int(r["거래금액(만원)"]), float(r["전용㎡"])), axis=1)
    return df


def hogang_style_filter(df: pd.DataFrame, target_m2: float, tol_m2: float, keyword: str, recent_n: int) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out = out[out["전용㎡"].between(target_m2 - tol_m2, target_m2 + tol_m2)]
    if keyword.strip():
        kw = keyword.strip().lower()
        out = out[out["단지명"].fillna("").str.lower().str.contains(kw)]
    out = out.sort_values("거래일자", ascending=False)
    if recent_n > 0:
        out = out.head(int(recent_n))
    return out


@st.cache_data(show_spinner=False)
def load_lawd_codes() -> pd.DataFrame:
    """법정동코드 로드 (시군구 5자리 + 리단위까지 확장 지원)

    파일 우선순위:
      1) lawd_full.xlsx / lawd_full.csv  (리단위까지 전부 들어있는 파일)
      2) lawd_codes.xlsx / lawd_codes.csv (기본 제공: 시군구 5자리)
      3) 내장 최소 목록

    반환 컬럼:
      - code: 원본 코드(리단위 등 길이 다양)
      - label: 표시명
      - sigungu: 실거래 API 호출용 시군구 5자리(code 앞 5자리)
    """
    candidates = [
        ("lawd_full.xlsx", "xlsx"),
        ("lawd_full.csv", "csv"),
        ("lawd_codes.xlsx", "xlsx"),
        ("lawd_codes.csv", "csv"),
    ]

    df = None
    for path, kind in candidates:
        try:
            if os.path.exists(path):
                if kind == "xlsx":
                    df = pd.read_excel(path, dtype=str)
                else:
                    df = pd.read_csv(path, dtype=str)
                if df is not None and not df.empty:
                    break
        except Exception:
            df = None

    if df is None or df.empty:
        df = pd.DataFrame([
            {"code": "11140", "label": "서울특별시 중구"},
            {"code": "27110", "label": "대구광역시 중구"},
            {"code": "28185", "label": "인천광역시 연수구"},
        ])

    cols_l = {c.lower(): c for c in df.columns}

    # Normalize to (code,label)
    if "code" in cols_l and ("label" in cols_l or "name" in cols_l):
        c_code = cols_l["code"]
        c_label = cols_l.get("label", cols_l.get("name"))
        out = df[[c_code, c_label]].rename(columns={c_code: "code", c_label: "label"}).copy()
    elif "lawd_cd" in cols_l and "sido" in cols_l and "sigungu" in cols_l:
        out = df[[cols_l["lawd_cd"], cols_l["sido"], cols_l["sigungu"]]].copy()
        out["code"] = out[cols_l["lawd_cd"]].astype(str)
        out["label"] = (out[cols_l["sido"]].astype(str) + " " + out[cols_l["sigungu"]].astype(str)).str.strip()
        out = out[["code", "label"]]
    else:
        out = df.iloc[:, :2].copy()
        out.columns = ["code", "label"]

    out["code"] = out["code"].astype(str).str.strip()
    out["label"] = out["label"].astype(str).str.strip()

    # 실거래 API는 시군구 5자리
    out["sigungu"] = (
        out["code"]
        .str.replace(r"\D", "", regex=True)
        .str.slice(0, 5)
        .str.zfill(5)
    )

    out = out.dropna().drop_duplicates(subset=["code", "label"]).sort_values(["label", "code"]).reset_index(drop=True)
    return out


def geocode_address(addr: str) -> Optional[Dict[str, float]]:
    if not addr.strip():
        return None
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": addr, "format": "json", "limit": 1}
    headers = {"User-Agent": "pricing-tool/1.0 (streamlit)"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
    except Exception:
        return None



EN_TO_KO_METRO = {
    "Seoul": "서울",
    "Incheon": "인천",
    "Suwon-si": "경기 수원시",
    "Seongnam-si": "경기 성남시",
    "Chungju-si": "충북 충주시",
    "Gyeonggi-do": "경기",
    "Chungcheongbuk-do": "충북",
}

# Minimal district translations (expand as needed)
SEOUL_GU = {
    "Jongno-gu": "종로구",
    "Jung-gu": "중구",
    "Yongsan-gu": "용산구",
    "Seongdong-gu": "성동구",
    "Gwangjin-gu": "광진구",
    "Gangseo-gu": "강서구",
    "Gangnam-gu": "강남구",
    "Songpa-gu": "송파구",
}

INCHEON_GU = {
    "Namdong-gu": "남동구",
    "Bupyeong-gu": "부평구",
    "Gyeyang-gu": "계양구",
    "Seo-gu": "서구",
}

def _guess_korean_admin(address: dict) -> Tuple[Optional[str], Optional[str]]:
    """Return (metro_ko, district_ko) best-effort from Nominatim reverse address."""
    if not address:
        return None, None

    # metro candidates
    metro = address.get("city") or address.get("state") or address.get("region") or address.get("province")
    if isinstance(metro, str):
        metro = metro.strip()

    # district candidates
    dist = address.get("borough") or address.get("city_district") or address.get("county") or address.get("municipality")
    if isinstance(dist, str):
        dist = dist.strip()

    # Convert metro
    metro_ko = None
    if metro:
        if "서울" in metro: metro_ko = "서울"
        elif "인천" in metro: metro_ko = "인천"
        elif "경기" in metro: metro_ko = "경기"
        elif "충북" in metro or "충청북도" in metro: metro_ko = "충북"
        elif metro in EN_TO_KO_METRO: metro_ko = EN_TO_KO_METRO[metro]
        elif metro == "Seoul": metro_ko = "서울"
        elif metro == "Incheon": metro_ko = "인천"

    # Convert district (gu level)
    dist_ko = None
    if dist:
        # already Korean with '구'
        if "구" in dist:
            dist_ko = dist.split()[0]
        else:
            if dist in SEOUL_GU:
                dist_ko = SEOUL_GU[dist]
                metro_ko = metro_ko or "서울"
            elif dist in INCHEON_GU:
                dist_ko = INCHEON_GU[dist]
                metro_ko = metro_ko or "인천"
            elif dist.endswith("-gu"):
                # fallback: strip and add '구' (not always correct but helps)
                base = dist.replace("-gu", "")
                dist_ko = base + "구"

    return metro_ko, dist_ko


@st.cache_data(ttl=3600, show_spinner=False)
def reverse_geocode(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18, "addressdetails": 1}
    headers = {"User-Agent": "pricing-tool/1.0 (streamlit)"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def infer_lawd_from_latlon(lawd_df: pd.DataFrame, lat: float, lon: float) -> Tuple[Optional[str], str]:
    """좌표(lat, lon)로부터 LAWD_CD(법정동 5자리 시군구)와 라벨을 추정합니다.

    1) lawd_df에 lat/lon 컬럼이 있으면: 가장 가까운 행정구역을 거리기반으로 선택(권장/안정)
    2) 없으면: Nominatim 역지오코딩(베스트에포트) 결과 문자열로 매칭(차단/오류 가능)
    """
    if lawd_df is None or lawd_df.empty:
        return None, "LAWD 목록이 비어있습니다"

    # 1) 거리 기반 (가장 안정적)
    if {"lat", "lon"}.issubset(set(lawd_df.columns)):
        df = lawd_df.copy()
        df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
        df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
        df = df.dropna(subset=["lat", "lon"])
        if not df.empty:
            df["dist"] = (df["lat"] - float(lat)) ** 2 + (df["lon"] - float(lon)) ** 2
            row = df.sort_values("dist").iloc[0]
            code5 = str(row["code"])[:5]
            label = str(row.get("label", ""))
            return code5, label if label else code5

    # 2) 폴백: 역지오코딩 + 문자열 매칭
    data = reverse_geocode(lat, lon)
    if not data:
        return None, "역지오코딩 실패(네트워크/차단 가능)"

    addr = data.get("address", {}) or {}
    metro_ko, dist_ko = _guess_korean_admin(addr)

    candidates: List[str] = []
    if metro_ko and dist_ko:
        candidates.append(f"{metro_ko} {dist_ko}")
    if dist_ko:
        candidates.append(dist_ko)

    labels = lawd_df["label"].astype(str)

    for cand in candidates:
        hit = lawd_df[labels.str.contains(re.escape(cand), na=False)]
        if not hit.empty:
            row = hit.iloc[0]
            return str(row["code"])[:5], str(row["label"])

    disp = str(data.get("display_name", ""))
    return None, f"매칭 실패: {disp[:80]}"

def static_map_url(lat: float, lon: float, zoom: int = 13, w: int = 1100, h: int = 620, markers: Optional[List[Tuple[float,float,str]]] = None) -> str:
    base = "https://staticmap.openstreetmap.de/staticmap.php"
    parts = [f"center={lat},{lon}", f"zoom={zoom}", f"size={w}x{h}", "maptype=mapnik"]
    if markers:
        for la, lo, c in markers:
            parts.append(f"markers={la},{lo},{c}1")
    else:
        parts.append(f"markers={lat},{lon},red1")
    return base + "?" + "&".join(parts)


@st.cache_data(ttl=3600, show_spinner=False)
def download_image(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "pricing-tool/1.0 (streamlit)"})
        if r.status_code != 200:
            return None
        return r.content
    except Exception:
        return None


LOCATION_PRESETS = {"도심 핵심(최상)": 1.12, "도심/중심권(상)": 1.06, "일반 주거권(중)": 1.00, "외곽/신도시(하)": 0.95, "입지 열위(최하)": 0.90}
BRAND_PRESETS = {"하이엔드(상급 라인)": 1.06, "상급(10대 브랜드 일반)": 1.03, "중급(지역 상위/준대형)": 1.00, "기타/무브랜드": 0.97}


def build_location_multiplier(grade_mult: float, subway_min: int, school_min: int, infra_min: int, dev: str,
                              w_subway: float, w_school: float, w_infra: float, w_dev: float) -> float:
    def score_by_min(m: int) -> float:
        if m <= 5: return 1.08
        if m <= 10: return 1.04
        if m <= 15: return 1.00
        if m <= 20: return 0.97
        return 0.94
    s_sub = score_by_min(subway_min)
    s_sch = score_by_min(school_min)
    s_inf = score_by_min(infra_min)
    s_dev = 1.00 if dev == "없음" else (1.03 if dev == "보통" else 1.06)
    w_total = max(1e-9, (w_subway + w_school + w_infra + w_dev))
    adj = (s_sub * w_subway + s_sch * w_school + s_inf * w_infra + s_dev * w_dev) / w_total
    return float(max(0.85, min(1.20, grade_mult * adj)))


def make_bar_chart_monthly_counts(df: pd.DataFrame) -> bytes:
    if df.empty:
        return b""
    tmp = df.copy()
    tmp["ym"] = tmp["거래일자"].dt.to_period("M").astype(str)
    g = tmp.groupby("ym").size().reset_index(name="count")
    g["ym_sort"] = pd.to_datetime(g["ym"] + "-01", errors="coerce")
    g = g.sort_values("ym_sort")
    fig = plt.figure(figsize=(7.2, 2.6), dpi=160)
    ax = fig.add_subplot(111)
    ax.bar(g["ym"], g["count"])
    ax.set_title("월별 거래건수")
    ax.tick_params(axis='x', rotation=45)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def register_korean_font():
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("KFONT", p))
                return "KFONT"
            except Exception:
                continue
    return None


def build_comp_table(filtered: pd.DataFrame, exclusive_ratio: float, topn: int = 10) -> pd.DataFrame:
    if filtered.empty:
        return pd.DataFrame()
    work = filtered.copy()
    if "자산" not in work.columns:
        work["자산"] = "기타"
    g = work.groupby(["자산", "단지명"]).agg(건수=("단지명", "size"), 평당가_전용=("평당가(원/평,전용)", "mean")).reset_index()
    g = g.sort_values(["건수", "평당가_전용"], ascending=[False, False]).head(topn)
    g["공급환산(전용→공급)"] = g["평당가_전용"] * float(exclusive_ratio)
    g = g.rename(columns={"평당가_전용": "평당가(전용)"})
    return g
def build_pdf_report(title: str, target_addr: str, geo: Optional[Dict[str, float]], product: str, lawd_label: str,
                     params_summary: Dict[str, Any], comp_table: pd.DataFrame, market_base_supply: float,
                     loc_mult: float, brand_mult: float, final_supply: float, map_png: Optional[bytes], chart_png: Optional[bytes]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14*mm, rightMargin=14*mm, topMargin=14*mm, bottomMargin=14*mm)
    font = register_korean_font()
    styles = getSampleStyleSheet()
    if font:
        styles.add(ParagraphStyle(name="KTitle", parent=styles["Title"], fontName=font, fontSize=20, leading=24, spaceAfter=8))
        styles.add(ParagraphStyle(name="KH2", parent=styles["Heading2"], fontName=font, fontSize=13, leading=16, spaceBefore=8, spaceAfter=6))
        styles.add(ParagraphStyle(name="KBody", parent=styles["BodyText"], fontName=font, fontSize=10.5, leading=14))
        styles.add(ParagraphStyle(name="KSmall", parent=styles["BodyText"], fontName=font, fontSize=9.2, leading=12, textColor=colors.grey))
        T, H2, B, S = styles["KTitle"], styles["KH2"], styles["KBody"], styles["KSmall"]
    else:
        T, H2, B, S = styles["Title"], styles["Heading2"], styles["BodyText"], styles["BodyText"]

    story = []
    story.append(Paragraph(title, T))
    story.append(Paragraph(f"기준일: {dt.date.today().isoformat()} · 상품: {product} · 지역: {lawd_label}", S))
    story.append(Spacer(1, 6))

    story.append(Paragraph("1. 사업 개요", H2))
    txt = f"대상지 주소: {target_addr or '-'}"
    if geo:
        txt += f"<br/>좌표: {geo['lat']:.6f}, {geo['lon']:.6f}"
    story.append(Paragraph(txt, B))
    story.append(Spacer(1, 6))
    if map_png:
        img = RLImage(io.BytesIO(map_png))
        img.drawHeight = 70*mm
        img.drawWidth = 180*mm
        story.append(img)
    else:
        story.append(Paragraph("지도 이미지를 불러오지 못했습니다.", S))
    story.append(Spacer(1, 10))

    story.append(Paragraph("2. 입지 분석", H2))
    story.append(Paragraph(f"입지 가중치: <b>{loc_mult:.3f}</b><br/>브랜드 가중치: <b>{brand_mult:.3f}</b>", B))
    story.append(Spacer(1, 6))

    story.append(Paragraph("3. 시장 환경", H2))
    story.append(Paragraph("국토부 실거래(매매) 데이터를 기반으로 면적대 필터 후 집계했습니다.", B))
    story.append(Spacer(1, 6))
    if chart_png:
        cimg = RLImage(io.BytesIO(chart_png))
        cimg.drawHeight = 45*mm
        cimg.drawWidth = 180*mm
        story.append(cimg)
    else:
        story.append(Paragraph("거래 추이 차트를 생성하지 못했습니다.", S))
    story.append(Spacer(1, 10))

    story.append(Paragraph("4. 인근 시세 현황 (비교 단지)", H2))
    if comp_table is not None and not comp_table.empty:
        cols = ["자산", "단지명", "건수", "평당가(전용)", "공급환산(전용→공급)"]
        data = [cols]
        for _, r in comp_table.iterrows():
            data.append([str(r.get("자산","-")), str(r["단지명"]), str(int(r["건수"])), f"{fmt0(float(r['평당가(전용)']))}", f"{fmt0(float(r['공급환산(전용→공급)']))}"])
        tbl = Table(data, colWidths=[18*mm, 62*mm, 15*mm, 40*mm, 45*mm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), font or "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,0), 9.5),
            ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
            ("FONTSIZE", (0,1), (-1,-1), 9.2),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph("비교 단지 집계 결과가 없습니다.", S))
    story.append(Spacer(1, 10))

    story.append(Paragraph("5. 분양가 산정 결과 (공급기준)", H2))
    result_rows = [
        ["구분", "값"],
        ["베이스(주변 매매 공급환산)", f"{fmt0(market_base_supply)} 원/평"],
        ["입지 가중치", f"{loc_mult:.3f}"],
        ["브랜드 가중치", f"{brand_mult:.3f}"],
        ["산정 분양가(공급)", f"{fmt0(final_supply)} 원/평"],
    ]
    rtbl = Table(result_rows, colWidths=[55*mm, 125*mm])
    rtbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2f5597")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), font or "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 10),
        ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(rtbl)
    story.append(Spacer(1, 8))

    story.append(Paragraph("부록. 입력 파라미터", H2))
    p_lines = [f"- {k}: {v}" for k, v in params_summary.items()]
    story.append(Paragraph("<br/>".join(p_lines), B))

    doc.build(story)
    return buf.getvalue()


# -----------------------------
# UI
# -----------------------------
st.set_page_config(page_title="분양가 산정 Tool (보고서 생성)", layout="wide")
st.title("분양가 산정 Tool — 보고서 자동 생성 (시세·입지·브랜드 기반)")
st.caption("대상지 위치/조건 입력 → 실거래 자동 조회 → ‘보고서(PDF)’ 형식으로 결과를 출력합니다.")

service_key = st.secrets.get("SERVICE_KEY", "").strip()
cfg = RtmsConfig(service_key=service_key)
lawd_df = load_lawd_codes()

with st.sidebar:
    st.subheader("대상지 입력")
    report_title = st.text_input("보고서 제목", value="사업지 시장조사 보고", key="r_title")
    target_name = st.text_input("사업명/메모(선택)", value="", key="t_name")
    target_addr = st.text_input("대상지 주소(지도)", value="", key="t_addr")

    st.divider()
    st.subheader("상품/기간")
    product = st.selectbox("상품", ["아파트", "오피스텔", "아파트+오피스텔"], index=0, key="s_product")
    end_ym = st.text_input("기준 계약년월(YYYYMM)", value=dt.date.today().strftime("%Y%m"), key="s_endym")
    months = st.number_input("최근 기간(개월)", min_value=1, max_value=36, value=12, step=1, key="s_months")

    st.divider()
    st.subheader("지도 기반 위치 선택")
    st.caption("지도를 **클릭**하면 빨간 핀이 이동합니다. (우클릭은 브라우저 메뉴가 떠서 지원이 어렵습니다)")

    st.divider()
    st.subheader("면적대/필터")

    target_m2 = st.number_input("기준 전용면적(㎡)", min_value=10.0, max_value=300.0, value=84.0, step=1.0, key="s_m2")
    tol_m2 = st.number_input("허용오차(±㎡)", min_value=0.0, max_value=20.0, value=3.0, step=0.5, key="s_tol")
    keyword = st.text_input("단지명 키워드(선택)", value="", key="s_kw")
    recent_n = st.number_input("최근 N건 (0=미사용)", min_value=0, max_value=200, value=50, step=5, key="s_n")

    st.divider()
    st.subheader("전용→공급 환산")
    exclusive_ratio = st.number_input("전용률(전용/공급)", min_value=0.50, max_value=0.95, value=0.70, step=0.01, key="s_exratio")

left, right = st.columns([1.12, 0.88])

with left:
    st.subheader("대상지 지도 (클릭으로 핀 이동)")

    # 초기 위치(서울 시청)
    if "pin_lat" not in st.session_state:
        st.session_state["pin_lat"] = 37.5665
    if "pin_lon" not in st.session_state:
        st.session_state["pin_lon"] = 126.9780

    # 주소 입력은 선택(원하면 주소 → 좌표로 이동)
    if target_addr.strip():
        geo = geocode_address(target_addr)
        if geo:
            st.session_state["pin_lat"] = geo["lat"]
            st.session_state["pin_lon"] = geo["lon"]
        else:
            st.info("주소를 찾지 못했습니다. 지도에서 직접 위치를 클릭해 주세요.")
    geo = {"lat": float(st.session_state["pin_lat"]), "lon": float(st.session_state["pin_lon"])}

    # Folium map (click to move pin)
    m = folium.Map(location=[geo["lat"], geo["lon"]], zoom_start=14, control_scale=True)
    folium.Marker([geo["lat"], geo["lon"]], tooltip="대상지(클릭 위치)", icon=folium.Icon(color="red")).add_to(m)

    # 비교 단지 마커(자동): 실거래 조회 후 비교단지 표 기반으로 지도에 표시
    @st.cache_data(ttl=86400, show_spinner=False)
    def geocode_complex_name(name: str, lawd_label: str) -> Optional[Dict[str, float]]:
        q = f"{name} {lawd_label}"
        return geocode_address(q)  # reuse search geocoder (best-effort)

    show_comp_markers = st.checkbox("비교단지 마커 표시", value=True, key="show_comp_markers")

    if show_comp_markers:
        flt_tmp = st.session_state.get("filtered_df", pd.DataFrame())
        if isinstance(flt_tmp, pd.DataFrame) and not flt_tmp.empty:
            comp_tmp = build_comp_table(flt_tmp, float(exclusive_ratio), topn=10)
            # Add up to 10 markers
            for _, row in comp_tmp.iterrows():
                nm = str(row.get("단지명","")).strip()
                asset = str(row.get("자산",""))
                if not nm:
                    continue
                g2 = geocode_complex_name(nm, lawd_label)
                if not g2:
                    continue
                color = "blue" if asset == "아파트" else ("green" if asset == "오피스텔" else "gray")
                tip = f"{asset} | {nm} | 공급환산 {fmt0(float(row.get('공급환산(전용→공급)',0)))}"
                folium.Marker([g2["lat"], g2["lon"]], tooltip=tip, icon=folium.Icon(color=color)).add_to(m)

    out = st_folium(m, height=420, use_container_width=True)

    # Update pin on click
    if out and out.get("last_clicked"):
        st.session_state["pin_lat"] = float(out["last_clicked"]["lat"])
        st.session_state["pin_lon"] = float(out["last_clicked"]["lng"])
        geo = {"lat": float(st.session_state["pin_lat"]), "lon": float(st.session_state["pin_lon"])}

    st.caption(f"핀 좌표: {geo['lat']:.6f}, {geo['lon']:.6f}")

    # Infer lawd code from pin
    inferred_cd, inferred_label = infer_lawd_from_latlon(lawd_df, geo["lat"], geo["lon"])
    if inferred_cd:
        st.success(f"자동 추정 시군구: {inferred_label}")
        selected_lawd_code = str(inferred_cd)
        lawd_cd = str(inferred_cd).replace('-', '')[:5].zfill(5)
        lawd_label = inferred_label
    else:
        st.warning(f"시군구 자동 추정 실패: {inferred_label}")
        # Fallback manual selection
        q2 = st.text_input("지역 검색(수동)", value="", key="fallback_search")
        view = lawd_df
        if q2.strip():
            qq = q2.strip().lower()
            view = view[view["label"].fillna("").str.lower().str.contains(qq)]
            if view.empty:
                st.info("검색 결과가 없습니다. lawd_codes.xlsx에 지역을 추가하세요.")
                view = lawd_df
        sel = st.selectbox("시군구 선택(수동)", view["label"].tolist(), index=0, key="fallback_lawd")
        sel_row = view[view["label"] == sel].iloc[0]
        lawd_cd = str(sel_row.get("sigungu", sel_row.get("code","")))
        selected_lawd_code = str(sel_row.get("code",""))
        lawd_label = sel

    st.divider()
    st.subheader("주변 시세(매매 실거래) 자동")
    st.caption("아파트/오피스텔 모두 ‘운영(일반) 실거래 API’ 엔드포인트로 조회합니다.")

    st.caption("아파트/오피스텔 모두 ‘운영(일반) 실거래 API’ 엔드포인트로 조회합니다.")
    run = st.button("실거래 조회 / 재계산", type="primary", key="btn_run")

    if "merged_df" not in st.session_state:
        st.session_state["merged_df"] = pd.DataFrame()
    if "did_query" not in st.session_state:
        st.session_state["did_query"] = False

    if "filtered_df" not in st.session_state:
        st.session_state["filtered_df"] = pd.DataFrame()
    if "market_base_supply" not in st.session_state:
        st.session_state["market_base_supply"] = 0.0

    if run:
        if not service_key:
            st.error("SERVICE_KEY가 비어 있어 실거래 조회를 할 수 없습니다. (Streamlit Cloud → Secrets)")
        else:
            today_ym = dt.date.today().strftime("%Y%m")
            use_end_ym = end_ym.strip()
            if (len(use_end_ym) != 6) or (not use_end_ym.isdigit()):
                use_end_ym = today_ym
                st.info(f"기준 계약년월이 올바르지 않아 오늘 기준({today_ym})으로 설정했습니다.")
            if use_end_ym > today_ym:
                st.info(f"미래 계약년월({use_end_ym})은 조회가 비어있을 수 있어 {today_ym}으로 조정했습니다.")
                use_end_ym = today_ym

            def fetch_range(month_count: int) -> pd.DataFrame:
                yms = ym_backwards(use_end_ym, int(month_count))
                dfs = []
                for ym in yms:
                    if product == "아파트":
                        dfm = get_apartment_trades(cfg, lawd_cd, ym)
                    elif product == "오피스텔":
                        dfm = get_officetel_trades(cfg, lawd_cd, ym)
                    else:
                        dfa = get_apartment_trades(cfg, lawd_cd, ym)
                        dfo = get_officetel_trades(cfg, lawd_cd, ym)
                        dfm = pd.concat([dfa, dfo], ignore_index=True) if (not dfa.empty or not dfo.empty) else pd.DataFrame()
                    if not dfm.empty:
                        dfs.append(dfm)
                return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

            with st.spinner("실거래가 조회 중... (월별 호출 후 합산)"):
                merged = fetch_range(int(months))

                # 자동 기간 확장: 선택 기간에 0건이면 최대 60개월까지 12개월씩 확장
                if merged.empty:
                    st.warning("선택 기간에 실거래가가 없어 자동으로 기간을 확장해 재조회합니다. (최대 60개월)")
                    for m2 in [24, 36, 48, 60]:
                        if m2 <= int(months):
                            continue
                        merged = fetch_range(m2)
                        if not merged.empty:
                            st.info(f"기간을 {m2}개월로 확장하여 실거래 {len(merged)}건을 찾았습니다.")
                            break

            
            # 결과를 세션에 저장(다른 섹션/보고서에서 재사용)
            st.session_state["merged_df"] = merged
            st.session_state["did_query"] = True

            st.subheader("조회 결과")

            if merged.empty:
                st.dataframe(pd.DataFrame([{"상태": "조회된 실거래가가 없습니다", "안내": "지역/기간/면적대/키워드를 확인해주세요"}]), use_container_width=True)
                st.session_state["filtered_df"] = pd.DataFrame()
                st.session_state["market_base_supply"] = 0.0
            else:
                st.caption(f"원본(기간 합산) {len(merged):,}건")
                st.dataframe(merged.sort_values(["거래일", "거래금액(만원)"], ascending=[False, False]).head(300), use_container_width=True)

                flt = hogang_style_filter(merged, float(target_m2), float(tol_m2), keyword, int(recent_n))
                st.caption(f"필터 적용 {len(flt):,}건 (전용 {target_m2}±{tol_m2}㎡, 키워드/최근N 적용)")

                if flt.empty:
                    st.dataframe(pd.DataFrame([{"상태": "필터 조건에서 거래가 없습니다", "안내": "허용오차를 늘리거나 키워드를 비우고 다시 조회해보세요"}]), use_container_width=True)
                    st.session_state["filtered_df"] = pd.DataFrame()
                    st.session_state["market_base_supply"] = 0.0
                else:
                    st.dataframe(flt.sort_values(["거래일", "거래금액(만원)"], ascending=[False, False]).head(300), use_container_width=True)
                    st.session_state["filtered_df"] = flt
                    try:
                        comp_tbl = build_comp_table(flt, float(exclusive_ratio), topn=200)
                        st.session_state["market_base_supply"] = float(comp_tbl["공급환산(전용→공급)"].mean()) if not comp_tbl.empty else 0.0
                    except Exception:
                        st.session_state["market_base_supply"] = 0.0

with right:
    st.subheader("입지/브랜드 가중치")
    loc_grade = st.selectbox("입지 등급(프리셋)", list(LOCATION_PRESETS.keys()), index=2, key="loc_grade")
    loc_base = LOCATION_PRESETS[loc_grade]
    subway_min = st.number_input("주요역 도보(분)", min_value=0, max_value=40, value=12, step=1, key="loc_sub")
    school_min = st.number_input("학군/학교 도보(분)", min_value=0, max_value=40, value=10, step=1, key="loc_sch")
    infra_min = st.number_input("상권/생활 도보(분)", min_value=0, max_value=40, value=8, step=1, key="loc_inf")
    dev = st.selectbox("개발호재/직주", ["없음", "보통", "강함"], index=1, key="loc_dev")
    with st.expander("입지 요소별 중요도(가중치)"):
        w_sub = st.slider("역 접근성", 0.0, 1.0, 0.35, 0.05, key="w_sub")
        w_sch = st.slider("학군/교육", 0.0, 1.0, 0.25, 0.05, key="w_sch")
        w_inf = st.slider("상권/생활", 0.0, 1.0, 0.25, 0.05, key="w_inf")
        w_dev = st.slider("호재/직주", 0.0, 1.0, 0.15, 0.05, key="w_dev")
    loc_mult = build_location_multiplier(loc_base, int(subway_min), int(school_min), int(infra_min), dev,
                                         float(w_sub), float(w_sch), float(w_inf), float(w_dev))

    brand_grade = st.selectbox("브랜드 등급(프리셋)", list(BRAND_PRESETS.keys()), index=2, key="brand_grade")
    brand_default = BRAND_PRESETS[brand_grade]
    brand_mult = float(st.slider("브랜드 가중치(조정)", 0.90, 1.12, float(brand_default), 0.01, key="brand_mult"))

    st.divider()
    base_supply = float(st.session_state.get("market_base_supply", 0.0))
    final_supply = base_supply * float(loc_mult) * float(brand_mult) if base_supply > 0 else 0.0
    st.metric("산정 분양가(공급)", "-" if final_supply <= 0 else f"{fmt0(final_supply)} 원/평")

    st.divider()
    st.subheader("보고서(PDF) 생성")
    topn = st.number_input("비교단지 표 Top N", min_value=5, max_value=30, value=10, step=1, key="topn")
    make_pdf = st.button("보고서 생성", key="btn_pdf")

    if "pdf_bytes" not in st.session_state:
        st.session_state["pdf_bytes"] = b""

    if make_pdf:
        flt = st.session_state.get("filtered_df", pd.DataFrame())
        if not isinstance(flt, pd.DataFrame) or flt.empty:
            st.error("실거래 조회 결과가 없습니다. 먼저 ‘실거래 조회 / 재계산’을 눌러주세요.")
        else:
            comp = build_comp_table(flt, float(exclusive_ratio), int(topn))
            chart_png = make_bar_chart_monthly_counts(flt)

            map_png = None
            if geo:
                markers = [(geo["lat"], geo["lon"], "red")]
                for i, name in enumerate(comp["단지명"].head(5).tolist()):
                    g2 = geocode_address(f"{name} {lawd_label}")
                    if g2:
                        color = ["blue", "green", "orange", "purple", "blue"][i % 5]
                        markers.append((g2["lat"], g2["lon"], color))
                url = static_map_url(geo["lat"], geo["lon"], zoom=13, w=1100, h=620, markers=markers)
                map_png = download_image(url)

            params_summary = {
                "사업명/메모": target_name or "-",
                "대상지 주소": target_addr or "-",
                "상품": product,
                "지역": lawd_label,
                "기간(개월)": int(months),
                "면적대(전용±)": f"{target_m2}±{tol_m2}㎡",
                "단지 키워드": keyword or "-",
                "최근N": int(recent_n),
                "전용률": float(exclusive_ratio),
                "입지 등급": loc_grade,
                "역/학군/상권/호재": f"{subway_min}/{school_min}/{infra_min}분, {dev}",
                "브랜드 등급": brand_grade,
            }

            pdf = build_pdf_report(
                title=f"{report_title} - {target_name}".strip(" -"),
                target_addr=target_addr,
                geo=geo,
                product=product,
                lawd_label=lawd_label,
                params_summary=params_summary,
                comp_table=comp,
                market_base_supply=base_supply,
                loc_mult=loc_mult,
                brand_mult=brand_mult,
                final_supply=final_supply,
                map_png=map_png,
                chart_png=chart_png,
            )
            st.session_state["pdf_bytes"] = pdf
            st.success("보고서 생성 완료! 아래에서 다운로드하세요.")

    if st.session_state.get("pdf_bytes"):
        st.download_button("PDF 다운로드", data=st.session_state["pdf_bytes"],
                           file_name=f"시장조사_보고서_{dt.date.today().strftime('%Y%m%d')}.pdf",
                           mime="application/pdf", key="dl_pdf")
