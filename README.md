# 분양가 산정 Tool (Streamlit)

## 개요
- 공공데이터포털 실거래가 API를 활용한 분양가 산정 보조용 Streamlit 앱
- 기준 계약년월: '년도/월' 분리 선택형
- 최근기간: 선택형
- 지도 클릭 시 법정동코드(10자리) 자동 추적(VWorld 역지오코딩)

## 배포 방법 (Streamlit Cloud)
1. GitHub에 아래 3개 파일을 레포 루트에 업로드
   - `streamlit_app.py`
   - `requirements.txt`
   - `README.md`
2. Streamlit Cloud에서 New App 생성
3. Main file path: `streamlit_app.py`
4. Secrets 설정

## Secrets 예시
```toml
# 공공데이터포털(실거래) 키
SERVICE_KEY = "발급받은_서비스키"

# (선택) VWorld 키: 지도 클릭 시 법정동코드 자동추적에 사용
VWORLD_KEY = "발급받은_VWorld_키"
```
