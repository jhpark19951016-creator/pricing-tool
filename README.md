분양가 산정 Tool (Streamlit) — 풀세트 v4 (HTTPError 처리)
이번 수정
실거래 API 호출 중 HTTPError가 발생해도 앱이 죽지 않도록 처리했습니다.
실패 시, HTTP 상태코드 + resultCode/resultMsg(있으면) + 응답 일부를 화면에서 확인할 수 있습니다.
서비스키는 화면에 마스킹 표시됩니다.
포함 기능
법정동코드: 지역 검색 + 드롭다운 선택 (lawd_codes.csv)
일반 사업성(원가+이윤+VAT) 공급기준 평당
분양가상한제(간이) 공급기준 평당
실거래 자동(아파트+오피스텔) + 호갱노노식 집계
시세기반 분양가(공급) = 실거래(전용) × 전용률 × α × β
권장 분양가(공급, 예시) = MIN(상한제(공급), 시세기반(공급))
배포
GitHub repo 루트에 파일 업로드 → Streamlit Cloud Reboot/재배포
Secrets: SERVICE_KEY 설정
