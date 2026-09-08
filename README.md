# aas-auto-fixer

AAS Auto Fixer

KOSMO(스마트제조혁신추진단) Validator에 AASX를 제출하기 전에, 매번 손으로 고치던 항목들을 일괄 보정해 주는 도구입니다.

AASX 파일을 업로드하면 수정된 AASX와 무엇을 왜 바꿨는지 정리한 수정 내역표를 돌려줍니다. 원본 파일은 변경하지 않습니다.

100% 통과를 보장하지 않습니다. 1차로 이 도구를 돌린 뒤 남는 오류를 직접 손보는 용도로 만들었습니다. 결과물은 반드시 Validator로 다시 확인하고, 원본 AASX는 따로 보관해 주세요.

사용법
AASX 파일 업로드
Auto Fix 실행 클릭
수정 내역과 자체 점검 결과 확인
수정된 AASX 다운로드 → Validator에 제출

특정 장비 전용이 아니라 어떤 AASX를 넣어도 같은 규칙으로 동작합니다. AAS(장비)가 여러 개 들어 있는 파일도 처리합니다.

무엇을 고치나요
1. ID 체계 통일

모든 식별자를 https://www.smart-factory.kr/ids/... 형식으로 맞춥니다.

대상	형식
AssetAdministrationShell	/ids/aas/{AAS idShort}/{version}/{revision}
Submodel	/ids/sm/{AAS idShort}/{SM idShort}/{version}/{revision}
ConceptDescription	/ids/cd/{CD idShort}/{version}/{revision}

version/revision은 파일에 명시된 값을 우선 쓰고, 없으면 1/0을 넣습니다. administration이 비어 있으면 같은 값으로 채웁니다.

장비명은 AASX 내부 폴더명이 아니라 AssetAdministrationShell의 idShort를 씁니다. AAS가 여러 개면 각 AAS의 submodel 참조를 따라가 Submodel별로 소유 AAS를 따로 판별합니다.

2. ID를 바꾸면 그 ID를 참조하던 곳도 함께 갱신

가장 중요한 부분입니다. ID만 바꾸고 참조를 그대로 두면 semanticId가 통째로 끊어져서 고치기 전보다 오류가 늘어납니다.

이 도구는 ID를 바꿀 때마다 문서 전체의 key value를 함께 리맵합니다. idShort로 매칭하는 방식이 아니라 ID 자체로 추적하기 때문에, idShort가 없는 SubmodelElementList 자식이나 idShort가 CD명과 다른 요소도 링크가 유지됩니다. Reference의 타입(ModelReference / ExternalReference)도 보존됩니다.

3. semanticId ↔ ConceptDescription 매칭

~의 semanticId와 매칭되는 ConceptDescription id가 없습니다 오류에 대응합니다. 순서대로 시도합니다.

이미 CD와 매칭되면 그대로 둠
같은 idShort를 가진 CD가 있으면 그 CD의 id로 연결
그래도 없으면 ConceptDescription을 새로 만들어 연결

semanticId가 아예 없는 요소도 같은 방식으로 만들어 붙입니다. Property뿐 아니라 SMC, MLP, File, SML, Entity 등 모든 SubmodelElement에 적용됩니다.

새로 만들어지는 CD의 내용 (idShort 기준)

항목	값
id	/ids/cd/{idShort}/1/0
description	Information describing {idShort}.
preferredName	카멜케이스 분해 (RejectCount → Reject Count)
dataType	Property의 valueType에서 추론 (xs:int → INTEGER_COUNT)
definition	description과 동일 문구

생성된 description은 형식만 채운 템플릿 문구입니다. 제출 전에 실제 의미에 맞게 다듬어 주세요. 수정 내역표에서 CD 신규 생성 항목만 보면 어떤 게 자동 생성된 건지 바로 확인됩니다.

또한 IDTA 표준 CD가 있어야 하는 항목이라도 표준 IRDI를 찾아주지는 않고 smart-factory.kr 주소로 새로 만듭니다. 표준을 따라야 하는 항목은 직접 확인이 필요합니다.

이 동작이 부담스러우면 옵션에서 끌 수 있습니다. 끄면 생성하지 않고 Warning으로만 표시합니다.

4. valueType ↔ dataType 정렬

Property "X"의 valueType과 ConceptDescription의 dataType이 매칭되지 않습니다 오류에 대응합니다. 실제 value 값을 근거로 세 단계로 판단합니다.

값이 선언된 타입과 안 맞으면 값 기준으로 valueType 재추론 xs:float + 값 1500 × 3000 → xs:string
valueType이 범용 xs:string인데 CD가 더 구체적이면 valueType을 끌어올림 xs:string + REAL_MEASURE + 값 125.5 → xs:double
그 외에는 CD의 dataType을 valueType에 맞춤 xs:date → DATE, xs:anyURI → IRI, xs:boolean → BOOLEAN

CD에 dataSpecificationIec61360이나 dataType이 아예 없으면 만들어 넣습니다. (실제 파일에서 CD의 상당수가 이 경우라 오류의 큰 비중을 차지합니다.)

xs:boolean 값은 소문자로 정규화합니다. XML boolean은 True가 아니라 true여야 합니다.

5. Property의 valueType / value 채우기

비어 있으면 idShort의 의미에서 타입을 추론해 채웁니다.

idShort 패턴	valueType	예시 값
...Temperature, ...Speed, ...Ratio 등	xs:double	1.0
...Count, ...Quantity, ...Index 등	xs:int	1
Is..., Has..., ...Detected 등	xs:boolean	true
...Date	xs:date	2026-09-08
...DateTime, ...Timestamp	xs:dateTime	2026-09-08T09:00:00+09:00
...Status, ...State	xs:string	Normal
그 외	xs:string	Sample

이미 값이 있으면 건드리지 않습니다.

6. idShort / displayName 정리
언더바 제거: Wheel_Operation → WheelOperation
KOSMO idShort 규칙 적용: 대문자로 시작, 2자 이상, 하이픈으로 끝나지 않음 arbitrary → Arbitrary, smAIModelNamePlate → SmAIModelNamePlate
displayName의 언더바도 함께 제거
7. SubmodelElementList 제약
제약	처리
AASd-120	직접 자식의 idShort 제거 (SML 자식은 idShort를 가질 수 없음)
AASd-108	typeValueListElement를 자식 타입에 맞게 설정
AASd-109	valueTypeListElement를 자식 valueType에 맞게 설정
AASd-107	semanticIdListElement를 자식 semanticId와 일치시킴. 자식들이 서로 다르면 해당 요소 제거
8. AAS 3.0 표준 제약
제약	처리
AASc-3a-009	REAL_MEASURE/INTEGER_MEASURE인데 unit·unitId가 없으면 REAL_COUNT/INTEGER_COUNT로 낮춤
IDTA 2-14 (문자열 최소 길이)	<value/> 처럼 비어 있는 선택 문자열 요소와 빈 컨테이너 제거
IEC61360 preferredName 필수	없으면 idShort 기반으로 생성
9. XSD 요소 순서 재정렬

AAS 3.0 XSD는 모든 복합타입이 xs:sequence라서, 자식 요소의 순서가 틀리면 그 자체로 스키마 오류입니다. 마지막 단계에서 문서 전체의 자식 순서를 스키마 순서대로 재정렬합니다.

10. 그 외
CD의 영문 description이 없으면 보충
idShort와 id가 완전히 동일한 중복 CD는 하나만 남김
AAS의 submodel 참조를 ModelReference / key type Submodel 형태로 정규화
WorkorderID → WorkOrderID 등 확인된 표기 오류 교정
네임스페이스, AASX 내부의 다른 파일(이미지 등), 원본 구조는 그대로 보존
자체 점검

수정이 끝나면 결과물을 다시 훑어서 남은 문제를 표로 보여줍니다. Validator에 올리기 전에 여기서 먼저 확인할 수 있습니다.

semanticId 없음 / CD 미매칭
CD dataType 없음, valueType ↔ dataType 불일치
value ↔ valueType 불일치
AAS ID 형식
AASc-3a-009, preferredName 누락
idShort 규칙 위반
빈 문자열 (IDTA 2-14)

aas-core3.0 패키지가 설치되어 있으면 AAS 3.0 표준 제약도 함께 검사합니다.

옵션
옵션	기본값	설명
매칭되는 CD가 없으면 자동 생성	켬	끄면 Warning으로만 표시
값이 형식에 안 맞으면 valueType 재추론	켬	xs:float + 1500 × 3000 → xs:string
IEC61360이 없는 CD에 골격 생성	끔	구조를 크게 추가하므로 필요할 때만
요소 순서 재정렬	켬	스키마 오류의 상당수가 순서 문제
수정 후 자체 점검	켬	
알려진 제약
XML 직렬화만 지원합니다. AASX 안에 JSON 형식의 AAS가 들어 있으면 건드리지 않고 경고만 표시합니다.
CD 정규화 시 병합이 일어날 수 있습니다. idShort가 같고 의미가 다른 CD가 여러 개면(예: 서로 다른 IRDI를 가진 ManufacturerName 2개) 하나로 합쳐집니다. Validator 통과에는 문제없지만 의미상 별개였던 항목이 합쳐지므로 원본 보관이 필요합니다.
생성되는 description은 템플릿 문구입니다. 실제 의미를 담고 있지 않습니다.
표준 IRDI를 찾아주지 않습니다. IDTA 표준 CD가 필요한 항목도 smart-factory.kr 주소로 새로 만듭니다.
