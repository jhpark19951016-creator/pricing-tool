# 분양가 산정 Tool — v6 (보고서 PDF 자동 생성)

## 목표
대상지 주소/조건 입력 → 주변 실거래 자동 조회 → **보고서(PDF) 형식 결과물** 출력

## 포함 기능
- 원가 입력 제거 (시세 기반)
- 실거래(OpenAPI) 자동 조회 (아파트/오피스텔)
- 면적대/키워드/최근 N 필터
- 전용→공급 환산(전용률)
- 입지 가중치 자동 산출 (프리셋 + 역/학군/상권/호재 + 중요도 조정)
- 브랜드 가중치 (프리셋 + 조정)
- 대상지 주소 입력 → 지도 표시
- **보고서(PDF) 생성 + 다운로드**

## 지도/비교단지 마커 안내
- 지도 이미지는 OSM Static Map(키 없음)을 사용합니다.
- 비교단지 마커는 단지명+지역을 지오코딩(베스트 에포트)하여 표시합니다.
  - 일부 단지는 주소 인식이 실패할 수 있습니다(그 경우 마커 누락).

## 배포
1) GitHub repo 루트에 업로드: `streamlit_app.py`, `requirements.txt`, `lawd_codes.csv`, `README.md`
2) Streamlit Cloud에서 main file = `streamlit_app.py`
3) Secrets 설정:
SERVICE_KEY = "공공데이터포털 디코딩 서비스키"


## v6.1 변경
- 아파트 실거래 조회 엔드포인트를 Dev에서 운영(일반)으로 변경: RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade
- 오피스텔은 기존 운영 엔드포인트 유지: RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade


## v6.2 변경(Fullset)
- 법정동코드 파일을 CSV 대신 **XLSX** 우선 로딩(lawd_codes.xlsx)
- CSV가 있으면 자동 fallback
- requirements에 openpyxl 추가
- 아파트/오피스텔 모두 운영(일반) 실거래 API 사용
