# 분양가 산정 Tool (Streamlit) — 풀세트 v3 (DuplicateElementId 해결)

## 이번 수정
StreamlitDuplicateElementId 오류는 **동일한 label을 가진 위젯이 여러 개 있을 때** 발생합니다.
이번 버전은 모든 입력 위젯에 `key=`를 부여해 재발 방지했습니다.

## 포함 기능
- 법정동코드: 지역 검색 + 드롭다운 선택 (lawd_codes.csv)
- 일반 사업성(원가+이윤+VAT) 공급기준 평당
- 분양가상한제(간이) 공급기준 평당
- 실거래 자동(아파트+오피스텔) + 호갱노노식 집계
- 시세기반 분양가(공급) = 실거래(전용) × 전용률 × α × β
- 권장 분양가(공급, 예시) = MIN(상한제(공급), 시세기반(공급))

## 파일
- streamlit_app.py
- requirements.txt
- lawd_codes.csv
- README.md

## 배포
GitHub repo 루트에 파일 업로드 → Streamlit Cloud Reboot/재배포
Secrets: SERVICE_KEY 설정
