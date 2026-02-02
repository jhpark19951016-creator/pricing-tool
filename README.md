# 분양가 산정 Tool (Streamlit) — 풀세트 v2

## 포함 기능
- 법정동코드 직접 입력 ❌ → **지역 검색 + 드롭다운 선택** ✅ (lawd_codes.csv)
- 일반 사업성(원가+이윤+VAT) **공급기준 평당**
- 분양가상한제(간이) **공급기준 평당**
- 주변 시세 자동(아파트+오피스텔): 국토부 실거래가 OpenAPI
- **원가 없이** 시세만으로 분양가 산정(공급기준):
  - 시세기반(공급) = 실거래 평당(전용) × 전용률(전용/공급) × α × β

## 파일
- streamlit_app.py
- requirements.txt
- lawd_codes.csv  (자주 쓰는 지역만 넣어서 확장)

## 배포(무료): Streamlit Community Cloud
1) GitHub에 새 repo 생성
2) 위 3개 파일을 repo 루트에 업로드
3) Streamlit Community Cloud에서 Create app → repo/branch 선택 → main file로 streamlit_app.py 선택
4) Secrets에 SERVICE_KEY 설정 후 Deploy

### Secrets 설정(필수)
Streamlit Cloud의 **App settings → Secrets** 에 아래를 추가:
SERVICE_KEY = "공공데이터포털 디코딩 서비스키"

## lawd_codes.csv 확장 방법
형식: code,name
- code는 시군구 5자리(예: 28260)
- name은 화면에 보일 지역명(예: 인천 서구)
