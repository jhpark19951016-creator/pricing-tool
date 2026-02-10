# 분양가 산정 Tool (Streamlit)

## v9 변경사항 (요청사항 #1)
- 지도 클릭 시 **법정동명(시/구/동)** 을 화면에 표시합니다.
  - Kakao: region_1/2/3depth_name 조합
  - VWorld: result[0].text 사용(가능할 때)

## Secrets
```toml
SERVICE_KEY = "공공데이터포털_키"
KAKAO_REST_API_KEY = "카카오_REST_API_키"

# (옵션) VWorld
VWORLD_KEY  = "VWorld_키"
```
