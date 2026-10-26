# AAS Validator Auto Fixer v2

추진단 Validator(IDTA 2-x·3-x·4-x, KOSMO-AAS·Submodel·SME·CD) 기준으로
반복되는 오류를 1차로 자동 보정하는 도구입니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

| 파일 | 역할 |
|---|---|
| `aas_fixer_core.py` | 보정·점검 로직 (Streamlit 없이 import 가능) |
| `app.py` | Streamlit 화면 |
| `llm_definitions.py` | CD 이름·정의 생성 (Claude API, 선택) |
| `requirements.txt` | streamlit, openpyxl, aas-core3.0, aas-core3.1 |
| `.streamlit/secrets.toml.example` | API 키 설정 예시 |

스크립트에서 바로 쓸 때:

```python
import aas_fixer_core as core
result = core.process_aasx(open("in.aasx", "rb").read(), {})
open("out.aasx", "wb").write(result["bytes"])
rows = core.build_report(next(iter(result["xml_texts"].values())),
                         result["package_info"])
```

## 사용 순서 (검토 엑셀 왕복)

| 단계 | 할 일 |
|---|---|
| ① | 화면 왼쪽에 **수정 전 원본 AASX** 를 올립니다. |
| ② | **Auto Fix 실행**. CD가 없던 요소에는 CD가 만들어지고, 빈 값에는 임의 예시값이 들어갑니다. |
| ③ | **검토 엑셀 받기**. 'CD 목록' 시트의 노란 빈칸(누락)과 '예시값' 시트의 '변경 필요' 행만 채우고 .xlsx 로 저장합니다. |
| ④ | 원본 AASX는 그대로 두고, 화면 오른쪽에 **채운 엑셀**을 올려 다시 실행합니다. 누락 0건이면 수정된 AASX를 받습니다. |

검토 엑셀 구성

| 시트 | 내용 |
|---|---|
| 사용법 | 진행 순서, 칸 색깔 설명, 남은 건수(수식으로 자동 집계), 작성 예시 |
| CD 목록 | AASX 안의 모든 SMC·Property·SML·File 등이 쓰는 CD 한 줄씩. 영문명·한글명·영문 정의·한글 정의, 상태(누락/완료 수식), CD id |
| 예시값 | 모든 Property·Range 의 경로, valueType, 현재값, **새 값**, 상태. 임의로 채운 값이 맨 위 |

- 칸 색깔: 진한 노랑 = 비어 있어 채워야 함(채우면 바로 색이 빠지고 상태가 '완료'로 바뀜),
  연한 노랑 = 수정 가능, 회색 = 바꿔도 반영 안 됨(IDTA·표준 CD 영문 원문, 식별 정보).
- 자동 생성 CD의 `[작성 필요]`/`[TBD]` 표시는 엑셀에서 빈칸으로 보입니다.
- 엑셀에서 고친 값은 기존 값도 덮어씁니다. 단, IDTA·표준 CD 영문 원문은 바꾸지 않습니다
  (원문이 비어 있으면 입력 가능).
- 정의를 고치면 CD description 도 같은 값으로, 한글명을 고치면 displayName(ko)도 같이 맞춥니다.
- 예시값은 valueType 형식에 맞는지 확인하고, 안 맞으면 반영하지 않고 경고합니다.
  Range 는 `최소..최대` 로 적습니다. 엑셀이 숫자·날짜로 바꾼 값도 원래 형식으로 되돌려 읽습니다.
- 다시 실행할 때 원본 AASX를 쓰는 이유: AutoFixed 파일에서는 이미 들어간 임의값(`1.0` 등)을
  진짜 값과 구분할 수 없습니다.

## AI로 CD 이름·정의 생성 (선택, 사용량 과금)

`.streamlit/secrets.toml` 에 `ANTHROPIC_API_KEY` 가 있을 때만 화면에 나타납니다.
키가 없으면 이 기능은 보이지 않고, 나머지 기능은 그대로 동작합니다.
켜면 `[작성 필요]`/`[TBD]` 표시인 자체 CD와 한글이 없는 IDTA·표준 CD를 대상으로
초안을 만들고, 결과는 위 검토 엑셀로 확인합니다. 외부로 보내는 정보는 idShort,
요소 종류, valueType, 단위, 상위·하위 idShort, 기존 정의이며 id URL은 보내지
않습니다. 사내 정보보호 정책상 외부 API 사용이 허용되는지 먼저 확인하세요.

## v2 → v3 변경

| 구분 | v2 | v3 |
|---|---|---|
| IDTA 템플릿 | 모든 CD id·Submodel semanticId를 smart-factory로 덮어씀 (DN/HD/TD semanticId 손상) | 표준 id(IRDI, admin-shell IRI 등) 보호 |
| 템플릿 semanticId에 CD 없음 | 같은 idShort CD로 semanticId를 바꿈 | IRDI → 같은 id로 CD 생성 / IRI → smart-factory CD + supplementalSemanticId·isCaseOf |
| SML 항목 idShort | 항상 제거 (AASd-120) | AAS 3.0일 때만 제거 (3.1에서 AASd-120 삭제) |
| IDTA 2-14 | 일부 문자열만 | 빈 assetType, 빈 File value, keys 없는 reference 등 확대 |
| AAS | id만 | assetKind=Type, globalAssetId 형식, 끊어진 ModelReference 복구 |
| Submodel | id·semanticId | + kind=Template |
| CD 텍스트 | 영문 자리표시자 추가 | description↔definition 보충, displayName, 요소 description=CD definition, 검토 엑셀(CD 목록) 왕복, 한글 누락·자리표시자 경고 |
| 타입 | xs:int | xs:integer (실수는 xs:float), 표준 CD는 보호 후 경고 |
| idShort | 하이픈 허용 | 3.0은 하이픈 제거, 3.1은 허용 |
| 패키지 | 처리 없음 | 디렉터리 엔트리, 파일명 언더바, 미참조 파일, aas-suppl, Content_Types, 썸네일 |
| 점검 | 자체 점검 몇 항목 | Validator 항목 번호별 리포트 + aas-core 3.0/3.1 자동 선택 |

## Validator 항목별 처리 범위

| 항목 | 자동 수정 | 비고 |
|---|---|---|
| IDTA 2-1 ~ 2-13 (스키마 구조) | 요소 순서 재정렬, 빈 컨테이너 정리 | 구조 자체가 틀린 경우는 aas-core 역직렬화 오류로 표시 |
| IDTA 2-14 문자열 최소 길이 | ✅ | 빈 assetType, `<value/>` 등 |
| IDTA 2-15/2-16 길이·패턴 | idShort 정규화 | |
| IDTA 3-x (AASd) | AASd-107/108/109, 3.0의 AASd-120 | 그 외는 aas-core 점검으로 표시 |
| IDTA 4-x (AASc-3a) | AASc-3a-009 (MEASURE↔unit) | |
| KOSMO-AAS #1 Thumbnail | relationship/defaultThumbnail 정합 | 이미지가 없으면 경고 |
| KOSMO-AAS #2~#6 | ✅ | |
| KOSMO-Submodel #1~#4 | ✅ | IDTA Submodel semanticId는 원본 유지 |
| KOSMO-SME #1, #3~#5 | ✅ | 표준 CD 불일치는 경고(가이드라인 7) |
| KOSMO-SME #2 특수 SME | 경고 (옵션으로 제거) | |
| KOSMO-CD #1~#5 | ✅ | 한글 이름·정의는 검토 엑셀로 채움 |

## 자동으로 고칠 수 없는 것 (사람 확인)

- 예시값의 의미와 정합 (편차 = 실측 − 목표, 합계, 효율, 사양 범위 등)
- 정의문의 내용 품질, 다른 장비 문구 잔존, 비식별 여부
- 계획서·AASX·가이던스 3자 일치
- 표준사전 IRDI의 정의·데이터형·단위 일치 여부
- IDTA 원본 CD 오류 (중복 언어 태그 등) — 수정하지 않음
