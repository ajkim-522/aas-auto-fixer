"""
AAS Validator Auto Fixer (v3) - Streamlit UI

실행:  streamlit run app.py   (가상환경에서는 python -m streamlit run app.py)
로직은 aas_fixer_core.py 에 있다.
"""

import os
import zipfile

import streamlit as st

import aas_fixer_core as core
import llm_definitions as llm


def read_api_key():
    """API 키는 화면에서 받지 않는다. secrets.toml → 환경변수 순으로 읽는다."""
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:  # noqa: BLE001  (secrets.toml 이 없으면 예외)
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


def read_setting(name, default):
    try:
        value = st.secrets.get(name)
    except Exception:  # noqa: BLE001
        value = None
    return value or os.environ.get(name) or default


st.set_page_config(
    page_title="AAS Validator Auto Fixer v3",
    page_icon="🔧",
    layout="wide",
)

st.title("🔧 AAS Validator Auto Fixer v3")
st.write(
    "추진단 Validator에서 반복되는 오류를 1차로 고치고, 사람이 채워야 하는 "
    "CD 이름·정의와 예시값은 검토 엑셀 한 장으로 모아 줍니다. IDTA 템플릿과 "
    "표준사전(IRDI) 식별자는 건드리지 않습니다."
)

# ------------------------------------------------------------------
# 진행 순서
# ------------------------------------------------------------------
steps = st.columns(4)
step_text = [
    ("① 원본 AASX 올리기", "수정 전 원본 파일을 올립니다."),
    ("② Auto Fix 실행", "오류를 고치고, 빈 CD·예시값은 표시해 둡니다."),
    ("③ 검토 엑셀 받아 채우기", "노란 빈칸(누락)만 채우고 .xlsx 로 저장합니다."),
    ("④ 엑셀과 함께 다시 실행", "원본 AASX + 채운 엑셀을 올려 다시 실행합니다. "
                              "누락 0건이면 끝."),
]
for column, (head, body) in zip(steps, step_text):
    with column.container(border=True):
        st.markdown(f"**{head}**")
        st.caption(body)

with st.expander("이 도구가 고치는 것 / 고치지 않는 것"):
    st.markdown(
        """
**자동으로 고치는 것**
- 식별자: AAS·Submodel·자체 CD id를 smart-factory 형식으로 맞추고, 그 id를
  가리키던 참조를 모두 따라 바꿉니다. 끊어진 ModelReference도 복구합니다.
- 템플릿 보호: DN·HD·TD의 semanticId, 표준 IRDI/IRI CD id와 영문 원문은 그대로
  둡니다. 표준 semanticId에 CD가 없으면 IRDI는 같은 id로, IRI는 smart-factory
  CD + supplementalSemanticId·isCaseOf로 연결합니다.
- assetKind=Type, Submodel kind=Template, globalAssetId 형식.
- valueType ↔ CD dataType, 실수 xs:float·정수 xs:integer, AASc-3a-009.
- 빈 문자열(IDTA 2-14), SML 제약, 요소 순서, AASX 패키지(파일·relationship·
  Content_Types·썸네일).

**표시만 하고 사람이 채우는 것 (검토 엑셀)**
- CD가 없어 새로 만든 CD의 이름·정의 → 엑셀 'CD 목록'에서 빈칸으로 보임
- 비어 있어 임의로 채운 예시값 → 엑셀 '예시값'에서 '변경 필요'로 보임

**경고만 하는 것**
- IDTA 원본 CD의 오류(중복 언어 태그, dataType 누락) — 가이드라인 7
- Operation·BasicEventElement·Capability (옵션으로 제거 가능)
- 예시값의 의미·정합, 비식별 여부, 계획서와의 일치
        """
    )

with st.expander("⚙️ 세부 옵션 (보통은 그대로 두세요)"):
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**모델**")
        create_missing_cd = st.checkbox(
            "매칭되는 CD가 없으면 ConceptDescription 생성", value=True
        )
        protect_standard_cd = st.checkbox(
            "IDTA/표준 CD 내용 보호 (dataType 등 수정 안 함)",
            value=True,
            help="끄면 표준 CD의 dataType도 valueType에 맞춰 바꿉니다.",
        )
        template_iri_strategy = st.radio(
            "템플릿 IRI semanticId에 CD가 없을 때",
            options=["smartfactory", "same"],
            format_func=lambda v: {
                "smartfactory": "smart-factory CD 생성 + 원래 IRI는 "
                                "supplementalSemanticId로 유지 (권장)",
                "same": "같은 IRI로 CD 생성",
            }[v],
        )
        refine_value_types = st.checkbox(
            "xs:string으로만 된 valueType 세분화", value=True
        )
        refine_by_value = st.checkbox(
            "값이 형식에 안 맞으면 valueType을 값 기준으로 재추론", value=True
        )
        fill_element_description = st.checkbox(
            "요소 description이 없으면 CD definition 복사", value=True
        )
        create_iec61360 = st.checkbox(
            "IEC61360 데이터 스펙이 없는 CD에 골격 생성", value=False
        )
    with col_b:
        st.markdown("**AAS / SML / 패키지**")
        asset_kind = st.selectbox(
            "assetKind", ["Type", "Instance", "(변경 안 함)"], index=0
        )
        submodel_kind = st.selectbox(
            "Submodel kind", ["Template", "Instance", "(변경 안 함)"], index=0
        )
        fix_global_asset_id = st.checkbox(
            "globalAssetId를 /ids/asset/{AAS명}/{v}/{r} 형식으로", value=True
        )
        sml_item_idshort = st.radio(
            "SML 항목 idShort",
            options=["auto", "never", "always"],
            format_func=lambda v: {
                "auto": "AAS 3.0일 때만 제거 (권장)",
                "never": "제거 안 함",
                "always": "항상 제거",
            }[v],
        )
        remove_special_sme = st.checkbox(
            "Operation / BasicEventElement / Capability 제거", value=False
        )
        rename_underscore_files = st.checkbox(
            "패키지 파일명 언더바 제거", value=True
        )
        remove_unreferenced = st.checkbox(
            "참조되지 않는 보조 파일·relationship 제거", value=True
        )
        reorder = st.checkbox("요소 순서 재정렬 (XSD sequence)", value=True)


# AI 정의 생성은 API 키가 설정된 경우에만 보인다 (사용량 과금)
api_key = read_api_key()
use_llm = False
domain = llm_model = llm_base_url = ""
llm_send_values = False
standard_cd_korean = True

if api_key:
    with st.expander("🤖 AI로 CD 이름·정의 초안 만들기 (Claude API, 사용량 과금)"):
        st.write(
            "검토 엑셀에서 누락으로 보일 칸을 AI 초안으로 미리 채웁니다. "
            "IDTA·표준 CD는 영문 원문을 두고 한글만 만듭니다. 결과는 검토 "
            "엑셀에서 반드시 확인하세요."
        )
        use_llm = st.checkbox("AI 초안 사용", value=False)
        domain = st.text_input(
            "장비 설명 (생성 품질에 큰 영향)",
            placeholder="예: 원통형 21700 셀을 권취하는 이차전지 와인딩기",
        )
        c1, c2 = st.columns(2)
        llm_model = c1.text_input(
            "모델", value=read_setting("ANTHROPIC_MODEL", llm.DEFAULT_MODEL)
        )
        llm_base_url = c2.text_input(
            "API 주소 (사내 게이트웨이 사용 시 변경)",
            value=read_setting("ANTHROPIC_BASE_URL", llm.DEFAULT_BASE_URL),
        )
        standard_cd_korean = st.checkbox(
            "IDTA·표준 CD에도 한글 이름·정의 추가 (영문 원문 유지)",
            value=True,
        )
        llm_send_values = st.checkbox(
            "Property 예시값도 함께 보내기", value=False,
            help="정의 품질이 조금 좋아지지만 예시값이 외부로 전송됩니다.",
        )
        st.caption(
            "외부로 보내는 정보: idShort, 요소 종류, valueType, 단위, 상위·하위 "
            "idShort, 기존 정의. id URL은 보내지 않습니다. 사내 정보보호 정책상 "
            "외부 API 사용이 허용되는지 먼저 확인하세요."
        )

st.divider()

# ------------------------------------------------------------------
# 입력
# ------------------------------------------------------------------
in1, in2 = st.columns(2)
with in1:
    uploaded_file = st.file_uploader(
        "① 원본 AASX (필수)", type=["aasx"],
        help="이전에 AutoFixed 한 파일보다 수정 전 원본을 권장합니다.",
    )
with in2:
    review_file = st.file_uploader(
        "④ 채운 검토 엑셀 (두 번째 실행부터)", type=["xlsx"],
        help="③에서 받은 엑셀의 노란 빈칸을 채워 저장한 파일입니다.",
    )

if uploaded_file is None:
    st.info("① 원본 AASX를 올리면 시작할 수 있습니다.")
    st.stop()

run_label = (
    "🔧 ② 엑셀 반영해서 다시 실행" if review_file is not None
    else "🔧 ② Auto Fix 실행"
)
run_clicked = st.button(run_label, type="primary", width="stretch")
file_key = (
    uploaded_file.name, uploaded_file.size,
    review_file.name if review_file is not None else None,
    review_file.size if review_file is not None else None,
)

if run_clicked:
    glossary_by_id, value_overrides = {}, {}
    if review_file is not None:
        try:
            glossary_by_id, value_overrides = core.parse_review_workbook(
                review_file.getvalue()
            )
        except Exception as error:  # noqa: BLE001
            st.error(f"검토 엑셀을 읽지 못했습니다: {error}")
            st.stop()

    options = {
        "create_missing_cd": create_missing_cd,
        "protect_standard_cd": protect_standard_cd,
        "template_iri_strategy": template_iri_strategy,
        "refine_value_types": refine_value_types,
        "refine_by_value": refine_by_value,
        "fill_element_description": fill_element_description,
        "create_iec61360": create_iec61360,
        "asset_kind": None if asset_kind.startswith("(") else asset_kind,
        "submodel_kind": (
            None if submodel_kind.startswith("(") else submodel_kind
        ),
        "fix_global_asset_id": fix_global_asset_id,
        "sml_item_idshort": sml_item_idshort,
        "remove_special_sme": remove_special_sme,
        "rename_underscore_files": rename_underscore_files,
        "remove_unreferenced": remove_unreferenced,
        "reorder": reorder,
        "fix_package": True,
        "glossary_by_id": glossary_by_id,
        "value_overrides": value_overrides,
        "standard_cd_korean": standard_cd_korean,
    }

    if use_llm and api_key:
        caches = st.session_state.setdefault("llm_cache", {})
        cache = caches.setdefault((domain.strip(), llm_model), {})
        progress_bar = st.progress(0.0, text="AI 초안 준비 중")

        def show_progress(done, total):
            ratio = done / total if total else 1.0
            progress_bar.progress(ratio, text=f"AI 초안 {done}/{total} 묶음")

        options["cd_text_generator"] = llm.make_generator(
            api_key, domain=domain, model=llm_model, base_url=llm_base_url,
            cache=cache, progress=show_progress,
        )
        options["llm_send_values"] = llm_send_values

    try:
        result = core.process_aasx(uploaded_file.getvalue(), options)
    except zipfile.BadZipFile:
        st.error("AASX(ZIP) 형식이 아닙니다. 파일이 손상되지 않았는지 확인하세요.")
        st.stop()
    except Exception as error:  # noqa: BLE001
        st.error("처리 중 오류가 났습니다. 아래 내용을 개발자에게 전달하세요.")
        st.exception(error)
        st.stop()

    if result["processed_files"]:
        first = next(iter(result["xml_texts"].values()))
        result["review_xlsx"] = core.export_review_workbook(
            first, uploaded_file.name
        )
        result["counts"] = core.review_counts(first)
        result["report"] = core.build_report(first, result["package_info"])
    result["applied"] = (len(glossary_by_id), len(value_overrides))
    st.session_state["result"] = result
    st.session_state["result_key"] = file_key

result = st.session_state.get("result")
if result is None or st.session_state.get("result_key") != file_key:
    st.stop()

if not result["processed_files"]:
    st.error(
        "AASX 안에서 AAS Environment XML(루트 태그 environment)을 찾지 못했습니다."
    )
    if result["skipped_json"]:
        st.warning(
            "JSON 직렬화 파일은 지원하지 않습니다: "
            + ", ".join(result["skipped_json"])
        )
    st.stop()

# ------------------------------------------------------------------
# 결과
# ------------------------------------------------------------------
logs = core.annotate_logs(result["logs"])
counts = result["counts"]
base_name = uploaded_file.name.rsplit(".", 1)[0]
remaining = counts["cd_missing"] + counts["value_todo"]

st.subheader("결과")
cd_applied, value_applied = result["applied"]
if review_file is not None:
    failed = [log for log in logs if log["구분"] == "예시값 반영 실패"]
    st.caption(
        f"검토 엑셀에서 CD {cd_applied}개 행, 예시값 {value_applied}개를 읽어 "
        f"반영했습니다." + (f" 예시값 {len(failed)}개는 형식이 맞지 않아 "
                          "반영하지 않았습니다('확인 필요' 탭)." if failed else "")
    )

m1, m2, m3, m4 = st.columns(4)
m1.metric("CD 누락", f"{counts['cd_missing']} / {counts['cd_total']}")
m2.metric("예시값 변경 필요", f"{counts['value_todo']}")
m3.metric("자동 수정", sum(1 for log in logs if log["결과"] == "Auto Fixed"))
m4.metric("확인 필요", sum(1 for log in logs if log["결과"] == "Warning"))

if remaining:
    st.warning(
        f"**다음 단계: ③ 검토 엑셀을 받아 노란 빈칸을 채우세요.** "
        f"CD 누락 {counts['cd_missing']}건, 예시값 {counts['value_todo']}건이 "
        "남아 있습니다. 채운 엑셀을 ④에 올리고, ①의 원본 AASX는 그대로 둔 채 "
        "다시 실행하세요."
    )
else:
    st.success(
        "CD 이름·정의와 예시값이 모두 채워졌습니다. 수정된 AASX를 받아 "
        "추진단 Validator로 최종 확인하세요."
    )

d1, d2, d3 = st.columns([2, 2, 1])
d1.download_button(
    "⬇️ ③ 검토 엑셀 받기",
    data=result["review_xlsx"],
    file_name=f"{base_name}_검토.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary" if remaining else "secondary",
    width="stretch",
)
d2.download_button(
    "⬇️ 수정된 AASX 받기",
    data=result["bytes"],
    file_name=f"{base_name}_AutoFixed.aasx",
    mime="application/octet-stream",
    type="secondary" if remaining else "primary",
    width="stretch",
)
d3.download_button(
    "수정 내역 CSV",
    data=core.logs_to_csv(logs),
    file_name=f"{base_name}_AutoFix_log.csv",
    mime="text/csv",
    width="stretch",
)

tab_report, tab_warn, tab_all = st.tabs(
    ["🔍 Validator 항목별 점검", "⚠️ 확인 필요", "📋 전체 수정 내역"]
)

with tab_report:
    st.caption(
        "수정된 파일을 추진단 Validator 항목 기준으로 다시 점검한 결과입니다. "
        "최종 판정은 추진단 Validator로 확인하세요."
    )
    st.dataframe(result["report"], width="stretch", hide_index=True)
    if any(row["결과"].startswith("➖") for row in result["report"]):
        st.caption(
            "`pip install aas-core3.0 aas-core3.1` 을 설치하면 "
            "IDTA 3-x/4-x 메타모델 제약도 검사합니다."
        )

with tab_warn:
    warnings = [log for log in logs if log["결과"] == "Warning"]
    if warnings:
        st.dataframe(
            warnings, width="stretch", hide_index=True,
            column_order=["검사항목", "구분", "요소", "수정 전", "수정 후"],
        )
    else:
        st.info("확인할 항목이 없습니다.")

with tab_all:
    categories = sorted({log["검사항목"] for log in logs})
    chosen = st.multiselect("검사항목으로 거르기", categories)
    shown = [log for log in logs if not chosen or log["검사항목"] in chosen]
    st.dataframe(
        shown, width="stretch", hide_index=True,
        column_order=["검사항목", "구분", "요소", "수정 전", "수정 후", "결과"],
    )
