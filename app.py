"""
AAS Validator Auto Fixer (v2)
=============================

KOSMO Validator + AAS 3.0 표준 제약 위반을 자동 보정하는 Streamlit 도구.

v2 핵심 변경
------------
* CD의 id를 바꾼 뒤, 그 id를 참조하던 모든 key value를 함께 리맵한다.
  (v1은 idShort가 일치하는 SME만 갱신해서, idShort 없는 SML 자식이나
   idShort가 CD명과 다른 요소의 semanticId가 통째로 끊어졌다.)
* semanticId가 어떤 CD와도 매칭되지 않으면 CD를 새로 만들어 연결한다.
* Property valueType과 CD dataType을 실제 value 값을 근거로 정렬한다.
* MEASURE 계열 dataType인데 unit이 없으면 COUNT 계열로 낮춘다(AASc-3a-009).
* idShort를 KOSMO 규칙(대문자 시작, 2자 이상)에 맞게 보정한다.
* embeddedDataSpecification 자식 순서를 실제 AASX 산출물 기준으로 정정.

실행:  streamlit run aas_auto_fixer.py
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import streamlit as st


# =========================================================
# 기본 설정
# =========================================================

BASE_URL = "https://www.smart-factory.kr/ids"

DEFAULT_VERSION = "1"
DEFAULT_REVISION = "0"

IEC61360_TEMPLATE_ID = (
    "https://admin-shell.io/DataSpecificationTemplates/"
    "DataSpecificationIec61360/3/0"
)

SAMPLE_DATETIME = "2026-09-08T09:00:00+09:00"
SAMPLE_DATE = "2026-09-08"
SAMPLE_TIME = "09:00:00"


st.set_page_config(
    page_title="AAS Validator Auto Fixer",
    page_icon="🔧",
    layout="wide",
)


CD_NAME_ALIAS = {
    "WorkorderID": "WorkOrderID",
    "Workorder": "WorkOrder",
    "WorkorderNumber": "ProductionOrderNumber",
    "ProductionorderNumber": "ProductionOrderNumber",
}


# =========================================================
# XSD 자식 요소 순서
# =========================================================

_REFERABLE_HEAD = [
    "extensions", "category", "idShort", "displayName", "description",
]

_SME_HEAD = _REFERABLE_HEAD + [
    "semanticId", "supplementalSemanticIds",
    "qualifiers", "embeddedDataSpecifications",
]

_REF_ORDER = ["type", "referredSemanticId", "keys"]


CHILD_ORDER = {
    "environment": [
        "assetAdministrationShells", "submodels", "conceptDescriptions",
    ],
    "assetAdministrationShell": _REFERABLE_HEAD + [
        "administration", "id", "embeddedDataSpecifications",
        "derivedFrom", "assetInformation", "submodels",
    ],
    "assetInformation": [
        "assetKind", "globalAssetId", "specificAssetIds",
        "assetType", "defaultThumbnail",
    ],
    "submodel": _REFERABLE_HEAD + [
        "administration", "id", "kind", "semanticId",
        "supplementalSemanticIds", "qualifiers",
        "embeddedDataSpecifications", "submodelElements",
    ],
    "conceptDescription": _REFERABLE_HEAD + [
        "administration", "id", "embeddedDataSpecifications", "isCaseOf",
    ],
    "administration": [
        "embeddedDataSpecifications", "version", "revision",
        "creator", "templateId",
    ],
    "reference": _REF_ORDER,
    "semanticId": _REF_ORDER,
    "semanticIdListElement": _REF_ORDER,
    "valueId": _REF_ORDER,
    "unitId": _REF_ORDER,
    "dataSpecification": _REF_ORDER,
    "globalAssetId": _REF_ORDER,
    "first": _REF_ORDER,
    "second": _REF_ORDER,
    "observed": _REF_ORDER,
    "key": ["type", "value"],
    "property": _SME_HEAD + ["valueType", "value", "valueId"],
    "multiLanguageProperty": _SME_HEAD + ["value", "valueId"],
    "range": _SME_HEAD + ["valueType", "min", "max"],
    "file": _SME_HEAD + ["value", "contentType"],
    "blob": _SME_HEAD + ["value", "contentType"],
    "referenceElement": _SME_HEAD + ["value"],
    "relationshipElement": _SME_HEAD + ["first", "second"],
    "annotatedRelationshipElement": _SME_HEAD + [
        "first", "second", "annotations",
    ],
    "entity": _SME_HEAD + [
        "statements", "entityType", "globalAssetId", "specificAssetIds",
    ],
    "operation": _SME_HEAD + [
        "inputVariables", "outputVariables", "inoutputVariables",
    ],
    "capability": list(_SME_HEAD),
    "basicEventElement": _SME_HEAD + [
        "observed", "direction", "state", "messageTopic", "messageBroker",
        "lastUpdate", "minInterval", "maxInterval",
    ],
    "submodelElementCollection": _SME_HEAD + ["value"],
    "submodelElementList": _SME_HEAD + [
        "orderRelevant", "semanticIdListElement", "typeValueListElement",
        "valueTypeListElement", "value",
    ],
    # 실제 AASX 산출물 확인 결과 dataSpecification 이 먼저다.
    "embeddedDataSpecification": [
        "dataSpecification", "dataSpecificationContent",
    ],
    "dataSpecificationIec61360": [
        "preferredName", "shortName", "unit", "unitId", "sourceOfDefinition",
        "symbol", "dataType", "definition", "valueFormat", "valueList",
        "value", "levelType",
    ],
    "qualifier": [
        "semanticId", "supplementalSemanticIds", "kind", "type",
        "valueType", "value", "valueId",
    ],
    "langStringTextType": ["language", "text"],
    "langStringNameType": ["language", "text"],
    "langStringPreferredNameTypeIec61360": ["language", "text"],
    "langStringShortNameTypeIec61360": ["language", "text"],
    "langStringDefinitionTypeIec61360": ["language", "text"],
}

DEFAULT_ORDER = list(_SME_HEAD)


# =========================================================
# XML 기본 함수
# =========================================================

def local_name(tag):
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def get_namespace(tag):
    if isinstance(tag, str) and tag.startswith("{"):
        return tag.split("}", 1)[0][1:]
    return ""


def ns_tag(element, name):
    namespace = get_namespace(element.tag)
    return f"{{{namespace}}}{name}" if namespace else name


def find_direct_child(element, child_name):
    if element is None:
        return None
    for child in element:
        if local_name(child.tag) == child_name:
            return child
    return None


def find_all_by_name(root, name):
    return [e for e in root.iter() if local_name(e.tag) == name]


def text_of(element):
    if element is None or not element.text:
        return ""
    return element.text.strip()


def get_idshort(element):
    return text_of(find_direct_child(element, "idShort"))


def get_id(element):
    value = text_of(find_direct_child(element, "id"))
    return value or None


def remove_underscore(text):
    return None if text is None else text.replace("_", "")


def insert_ordered(parent, child):
    order = CHILD_ORDER.get(local_name(parent.tag), DEFAULT_ORDER)
    name = local_name(child.tag)

    if name not in order:
        parent.append(child)
        return

    rank = order.index(name)
    index = len(parent)

    for position, existing in enumerate(parent):
        existing_name = local_name(existing.tag)
        existing_rank = (
            order.index(existing_name)
            if existing_name in order
            else len(order) + 1
        )
        if existing_rank > rank:
            index = position
            break

    parent.insert(index, child)


def reorder_children(element):
    order = CHILD_ORDER.get(local_name(element.tag))
    if order is None:
        return False

    children = list(element)
    if len(children) < 2:
        return False

    def rank_of(child):
        name = local_name(child.tag)
        return order.index(name) if name in order else len(order) + 1

    ranks = [rank_of(child) for child in children]
    if all(ranks[i] <= ranks[i + 1] for i in range(len(ranks) - 1)):
        return False

    ordered = [
        child
        for _, _, child in sorted(
            ((rank_of(c), i, c) for i, c in enumerate(children)),
            key=lambda item: (item[0], item[1]),
        )
    ]

    for child in children:
        element.remove(child)
    for child in ordered:
        element.append(child)
    return True


def reorder_all(root, logs):
    count = 0
    for element in list(root.iter()):
        if reorder_children(element):
            count += 1
    if count:
        logs.append({
            "구분": "요소 순서 (XSD sequence)",
            "요소": "(전체)",
            "수정 전": f"{count}개 요소 순서 불일치",
            "수정 후": "스키마 순서로 재정렬",
            "결과": "Auto Fixed",
        })


# =========================================================
# Reference
# =========================================================

def get_reference_value(reference):
    """Reference 의 마지막 key value."""
    keys = find_direct_child(reference, "keys")
    if keys is None:
        return None

    last = None
    for key in keys:
        if local_name(key.tag) != "key":
            continue
        value = text_of(find_direct_child(key, "value"))
        if value:
            last = value
    return last


def get_reference_type(reference):
    return text_of(find_direct_child(reference, "type")) or None


def set_reference(
    reference,
    new_value,
    reference_type="ExternalReference",
    key_type="GlobalReference",
):
    if reference is None:
        return False

    type_element = find_direct_child(reference, "type")
    if type_element is None:
        type_element = ET.Element(ns_tag(reference, "type"))
        insert_ordered(reference, type_element)
    type_element.text = reference_type

    keys = find_direct_child(reference, "keys")
    if keys is None:
        keys = ET.Element(ns_tag(reference, "keys"))
        insert_ordered(reference, keys)

    for child in list(keys):
        keys.remove(child)

    key = ET.SubElement(keys, ns_tag(keys, "key"))
    key_type_element = ET.SubElement(key, ns_tag(key, "type"))
    key_type_element.text = key_type
    value_element = ET.SubElement(key, ns_tag(key, "value"))
    value_element.text = new_value
    return True


def get_semantic_id_value(element):
    return get_reference_value(find_direct_child(element, "semanticId"))


def create_semantic_id(element, reference_value):
    """semanticId 는 그 자체가 reference_t 이다 (<reference> 래퍼 없음)."""
    if find_direct_child(element, "semanticId") is not None:
        return False

    semantic_id = ET.Element(ns_tag(element, "semanticId"))

    type_element = ET.SubElement(semantic_id, ns_tag(semantic_id, "type"))
    type_element.text = "ExternalReference"

    keys = ET.SubElement(semantic_id, ns_tag(semantic_id, "keys"))
    key = ET.SubElement(keys, ns_tag(keys, "key"))
    key_type = ET.SubElement(key, ns_tag(key, "type"))
    key_type.text = "GlobalReference"
    value = ET.SubElement(key, ns_tag(key, "value"))
    value.text = reference_value

    insert_ordered(element, semantic_id)
    return True


def remap_all_keys(root, mapping, logs, label):
    """
    문서 전체의 key/value 를 mapping 에 따라 치환한다.
    Reference 타입(ModelReference/ExternalReference)은 그대로 보존된다.
    """
    if not mapping:
        return 0

    count = 0
    for key in find_all_by_name(root, "key"):
        value_element = find_direct_child(key, "value")
        old = text_of(value_element)
        if old and old in mapping and mapping[old] != old:
            value_element.text = mapping[old]
            count += 1

    if count:
        logs.append({
            "구분": f"{label} 참조 리맵",
            "요소": "(전체)",
            "수정 전": f"{len(mapping)}개 ID 변경",
            "수정 후": f"참조 {count}건 동기화",
            "결과": "Auto Fixed",
        })
    return count


# =========================================================
# idShort 정규화 (KOSMO 규칙)
#
# 대문자로 시작, 영문/숫자/'-'/'_' 사용, '-' 로 끝나지 않음, 2자 이상
# (사용자 요구사항에 따라 '_' 는 제거한다)
# =========================================================

IDSHORT_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9_-]*[A-Za-z0-9_]$")


def sanitize_idshort(value):
    if not value:
        return value

    result = value.replace("_", "")
    result = re.sub(r"[^A-Za-z0-9\-]", "", result)
    result = result.rstrip("-")

    if not result:
        return None

    if not result[0].isalpha():
        result = "X" + result

    result = result[0].upper() + result[1:]

    if len(result) < 2:
        result = result + "X"

    return result


def clean_idshorts(root, logs):
    for element in list(root.iter()):
        idshort_element = find_direct_child(element, "idShort")
        if idshort_element is None or not idshort_element.text:
            continue

        old_value = idshort_element.text.strip()
        new_value = sanitize_idshort(old_value)

        if not new_value or old_value == new_value:
            continue

        idshort_element.text = new_value
        reason = (
            "언더바 제거"
            if old_value.replace("_", "") == new_value
            else "KOSMO idShort 규칙"
        )
        logs.append({
            "구분": f"idShort ({reason})",
            "요소": local_name(element.tag),
            "수정 전": old_value,
            "수정 후": new_value,
            "결과": "Auto Fixed",
        })


def clean_display_names(root, logs):
    for display_name in find_all_by_name(root, "displayName"):
        for lang_string in display_name:
            text_element = find_direct_child(lang_string, "text")
            if text_element is None or not text_element.text:
                continue

            old_value = text_element.text
            new_value = remove_underscore(old_value)
            if old_value == new_value:
                continue

            text_element.text = new_value
            logs.append({
                "구분": "displayName",
                "요소": "",
                "수정 전": old_value,
                "수정 후": new_value,
                "결과": "Auto Fixed",
            })


# =========================================================
# Version / Revision
# =========================================================

_NUMERIC = r"\d+(?:\.\d+)*"


def extract_version_revision_from_url(url):
    if not url:
        return None, None

    match = re.search(r"/([^/]+)/([^/]+)$", url.strip().rstrip("/"))
    if not match:
        return None, None

    version, revision = match.group(1), match.group(2)
    if re.fullmatch(_NUMERIC, version) and re.fullmatch(_NUMERIC, revision):
        return version, revision
    return None, None


def get_administration_version_revision(element):
    administration = find_direct_child(element, "administration")
    if administration is None:
        return None, None
    return (
        text_of(find_direct_child(administration, "version")) or None,
        text_of(find_direct_child(administration, "revision")) or None,
    )


def determine_version_revision(element):
    version, revision = get_administration_version_revision(element)
    id_version, id_revision = extract_version_revision_from_url(
        get_id(element)
    )
    version = version or id_version or DEFAULT_VERSION
    revision = revision or id_revision or DEFAULT_REVISION
    return str(version), str(revision)


def ensure_administration(element, version, revision):
    changed = False

    administration = find_direct_child(element, "administration")
    if administration is None:
        administration = ET.Element(ns_tag(element, "administration"))
        insert_ordered(element, administration)
        changed = True

    for name, target in (("version", version), ("revision", revision)):
        child = find_direct_child(administration, name)
        if child is None:
            child = ET.Element(ns_tag(administration, name))
            insert_ordered(administration, child)
            child.text = str(target)
            changed = True
        elif text_of(child) != str(target):
            child.text = str(target)
            changed = True

    return changed


# =========================================================
# URL 생성
# =========================================================

def build_sm_url(equipment_name, idshort, version, revision):
    return (
        f"{BASE_URL}/sm/{remove_underscore(equipment_name)}/"
        f"{remove_underscore(idshort)}/{version}/{revision}"
    )


def get_last_id_token(current_id):
    if not current_id:
        return None
    parts = current_id.rstrip("/").split("/")
    if len(parts) >= 3:
        if (
            re.fullmatch(_NUMERIC, parts[-1])
            and re.fullmatch(_NUMERIC, parts[-2])
        ):
            return parts[-3]
    return parts[-1]


def canonical_cd_name(idshort, old_id=None):
    clean = remove_underscore(idshort)
    token = get_last_id_token(old_id)
    if token in CD_NAME_ALIAS:
        return CD_NAME_ALIAS[token]
    if clean in CD_NAME_ALIAS:
        return CD_NAME_ALIAS[clean]
    return clean


def build_cd_url(idshort, version, revision, old_id=None):
    return (
        f"{BASE_URL}/cd/{canonical_cd_name(idshort, old_id)}/"
        f"{version}/{revision}"
    )


# =========================================================
# AAS / Submodel
# =========================================================

def build_submodel_owner_map(root):
    """submodel id -> AAS idShort. Submodel id 재작성 전에 호출."""
    owner_map = {}
    for shell in find_all_by_name(root, "assetAdministrationShell"):
        shell_name = remove_underscore(get_idshort(shell))
        if not shell_name:
            continue
        container = find_direct_child(shell, "submodels")
        if container is None:
            continue
        for reference in container:
            value = get_reference_value(reference)
            if value:
                owner_map[value] = shell_name
    return owner_map


def get_default_equipment_name(root, xml_path):
    for shell in find_all_by_name(root, "assetAdministrationShell"):
        idshort = get_idshort(shell)
        if idshort:
            return remove_underscore(idshort)

    parts = xml_path.replace("\\", "/").split("/")
    if len(parts) >= 2:
        return remove_underscore(parts[-2])
    return remove_underscore(parts[-1].split(".")[0])


def build_aas_url(idshort, version, revision):
    return (
        f"{BASE_URL}/aas/{remove_underscore(idshort)}/{version}/{revision}"
    )


def fix_aas_administration(root, logs):
    """AAS 의 administration 과 id 를 규격에 맞게 정리하고 id 매핑을 반환."""
    id_map = {}

    for shell in find_all_by_name(root, "assetAdministrationShell"):
        idshort = get_idshort(shell)
        version, revision = determine_version_revision(shell)
        before = get_administration_version_revision(shell)

        if ensure_administration(shell, version, revision):
            logs.append({
                "구분": "AAS Administration",
                "요소": idshort,
                "수정 전": f"{before[0]} / {before[1]}",
                "수정 후": f"{version} / {revision}",
                "결과": "Auto Fixed",
            })

        if not idshort:
            continue

        old_id = get_id(shell)
        new_id = build_aas_url(idshort, version, revision)

        id_element = find_direct_child(shell, "id")
        if id_element is None:
            id_element = ET.Element(ns_tag(shell, "id"))
            insert_ordered(shell, id_element)

        if old_id != new_id:
            if old_id:
                id_map[old_id] = new_id
            id_element.text = new_id
            logs.append({
                "구분": "AAS ID",
                "요소": idshort,
                "수정 전": old_id or "없음",
                "수정 후": new_id,
                "결과": "Auto Fixed",
            })

    return id_map


def fix_submodels(root, owner_map, default_equipment, logs):
    replacement_map = {}
    idshort_map = {}

    for submodel in find_all_by_name(root, "submodel"):
        idshort = get_idshort(submodel)
        if not idshort:
            logs.append({
                "구분": "Submodel",
                "요소": "(idShort 없음)",
                "수정 전": get_id(submodel) or "",
                "수정 후": "건너뜀",
                "결과": "Warning",
            })
            continue

        old_id = get_id(submodel) or ""
        equipment = owner_map.get(old_id) or default_equipment
        version, revision = determine_version_revision(submodel)
        new_url = build_sm_url(equipment, idshort, version, revision)

        id_element = find_direct_child(submodel, "id")
        if id_element is None:
            id_element = ET.Element(ns_tag(submodel, "id"))
            insert_ordered(submodel, id_element)

        if old_id != new_url:
            if old_id:
                replacement_map[old_id] = new_url
            id_element.text = new_url
            logs.append({
                "구분": "Submodel ID",
                "요소": idshort,
                "수정 전": old_id or "없음",
                "수정 후": new_url,
                "결과": "Auto Fixed",
            })
        idshort_map[idshort] = new_url

        semantic_id = find_direct_child(submodel, "semanticId")
        if semantic_id is None:
            create_semantic_id(submodel, new_url)
            logs.append({
                "구분": "Submodel SemanticID",
                "요소": idshort,
                "수정 전": "없음",
                "수정 후": new_url,
                "결과": "Auto Fixed",
            })
        else:
            old_semantic = get_reference_value(semantic_id)
            if old_semantic != new_url:
                set_reference(semantic_id, new_url)
                logs.append({
                    "구분": "Submodel SemanticID",
                    "요소": idshort,
                    "수정 전": old_semantic or "없음",
                    "수정 후": new_url,
                    "결과": "Auto Fixed",
                })

        ensure_administration(submodel, version, revision)

    return replacement_map, idshort_map


def normalize_aas_submodel_references(root, idshort_map, logs):
    """AAS 의 submodel 참조를 ModelReference/Submodel 형태로 맞춘다."""
    valid_ids = set(idshort_map.values())

    for shell in find_all_by_name(root, "assetAdministrationShell"):
        shell_name = get_idshort(shell)
        container = find_direct_child(shell, "submodels")
        if container is None:
            continue

        for reference in container:
            value = get_reference_value(reference)
            if not value:
                continue

            new_value = value
            if value not in valid_ids:
                token = remove_underscore(get_last_id_token(value) or "")
                if token in idshort_map:
                    new_value = idshort_map[token]
                else:
                    logs.append({
                        "구분": "AAS Submodel Reference",
                        "요소": shell_name,
                        "수정 전": value,
                        "수정 후": "대응 Submodel 없음 - 수동 확인 필요",
                        "결과": "Warning",
                    })
                    continue

            keys = find_direct_child(reference, "keys")
            key_count = (
                len([k for k in keys if local_name(k.tag) == "key"])
                if keys is not None else 0
            )
            current_type = get_reference_type(reference)

            if (
                value == new_value
                and current_type == "ModelReference"
                and key_count == 1
            ):
                continue

            set_reference(
                reference, new_value,
                reference_type="ModelReference", key_type="Submodel",
            )
            logs.append({
                "구분": "AAS Submodel Reference",
                "요소": shell_name,
                "수정 전": f"{current_type} / {value}",
                "수정 후": f"ModelReference / {new_value}",
                "결과": "Auto Fixed",
            })


# =========================================================
# ConceptDescription
# =========================================================

def get_concept_descriptions(root):
    return find_all_by_name(root, "conceptDescription")


def normalize_concept_descriptions(root, logs):
    """
    CD id 를 /ids/cd/{name}/{version}/{revision} 로 통일하고
    old_id -> new_id 매핑을 반환한다.
    """
    id_map = {}

    for cd in get_concept_descriptions(root):
        idshort = get_idshort(cd)
        if not idshort:
            continue

        old_id = get_id(cd)
        version, revision = determine_version_revision(cd)
        new_id = build_cd_url(idshort, version, revision, old_id)

        id_element = find_direct_child(cd, "id")
        if id_element is None:
            id_element = ET.Element(ns_tag(cd, "id"))
            insert_ordered(cd, id_element)

        if old_id != new_id:
            if old_id:
                id_map[old_id] = new_id
            id_element.text = new_id
            logs.append({
                "구분": "ConceptDescription ID",
                "요소": idshort,
                "수정 전": old_id or "없음",
                "수정 후": new_id,
                "결과": "Auto Fixed",
            })

        ensure_administration(cd, version, revision)

    return id_map


def remove_duplicate_concept_descriptions(root, logs):
    for parent in list(root.iter()):
        seen = set()
        for child in list(parent):
            if local_name(child.tag) != "conceptDescription":
                continue
            key = (get_idshort(child), get_id(child))
            if key not in seen:
                seen.add(key)
                continue
            parent.remove(child)
            logs.append({
                "구분": "Duplicate CD",
                "요소": key[0],
                "수정 전": key[1] or "",
                "수정 후": "중복 1건 제거",
                "결과": "Auto Fixed",
            })


def get_concept_descriptions_container(root):
    container = find_direct_child(root, "conceptDescriptions")
    if container is None:
        container = ET.Element(ns_tag(root, "conceptDescriptions"))
        insert_ordered(root, container)
    return container


def build_cd_index(root):
    """(id 집합, idShort -> id (유일한 경우만), idShort 집합)"""
    ids = set()
    by_idshort = {}
    all_idshorts = set()

    for cd in get_concept_descriptions(root):
        cd_id = get_id(cd)
        idshort = get_idshort(cd)
        if cd_id:
            ids.add(cd_id)
        if idshort:
            all_idshorts.add(idshort)
        if cd_id and idshort:
            by_idshort.setdefault(idshort, set()).add(cd_id)

    unique = {
        name: next(iter(values))
        for name, values in by_idshort.items()
        if len(values) == 1
    }
    return ids, unique, all_idshorts


# =========================================================
# SME <-> CD semanticId 해소
# =========================================================

SME_TYPES = {
    "property", "multiLanguageProperty", "range", "file", "blob",
    "referenceElement", "relationshipElement", "annotatedRelationshipElement",
    "entity", "operation", "capability", "basicEventElement",
    "submodelElementCollection", "submodelElementList",
}

SME_DEFAULT_DATATYPE = {
    "multiLanguageProperty": "STRING_TRANSLATABLE",
    "file": "FILE",
    "blob": "BLOB",
    "referenceElement": "IRI",
}


def name_from_semantic_id(value):
    """semanticId URL/IRDI 에서 CD 이름 후보를 만든다."""
    if not value:
        return None

    token = get_last_id_token(value)
    candidate = sanitize_idshort(token)
    if (
        candidate
        and len(candidate) >= 2
        and re.search(r"[A-Za-z]{2}", candidate)
    ):
        return candidate
    return None


def unique_cd_name(base, used):
    name = base or "GeneratedConcept"
    if name not in used:
        used.add(name)
        return name
    index = 2
    while f"{name}{index}" in used:
        index += 1
    result = f"{name}{index}"
    used.add(result)
    return result


def create_concept_description(root, name, cd_id, datatype, used_names):
    container = get_concept_descriptions_container(root)
    cd = ET.SubElement(container, ns_tag(container, "conceptDescription"))

    idshort = ET.SubElement(cd, ns_tag(cd, "idShort"))
    idshort.text = name

    description = ET.SubElement(cd, ns_tag(cd, "description"))
    add_lang_string(
        description, "langStringTextType", f"Information describing {name}."
    )

    administration = ET.SubElement(cd, ns_tag(cd, "administration"))
    version = ET.SubElement(administration, ns_tag(administration, "version"))
    version.text = DEFAULT_VERSION
    revision = ET.SubElement(
        administration, ns_tag(administration, "revision")
    )
    revision.text = DEFAULT_REVISION

    id_element = ET.SubElement(cd, ns_tag(cd, "id"))
    id_element.text = cd_id

    build_iec61360(cd, name, datatype)
    used_names.add(name)
    return cd


def resolve_semantic_ids(root, logs, create_missing=True):
    """
    1) semanticId 가 이미 CD id 와 매칭되면 그대로 둔다.
    2) 매칭되지 않으면 같은 idShort 의 CD 로 연결한다.
    3) 그래도 없으면 CD 를 새로 만들어 연결한다.
    4) semanticId 자체가 없으면 위 규칙으로 만들어 붙인다.
    """
    cd_ids, cd_by_idshort, used_names = build_cd_index(root)

    orphan_groups = {}
    no_semantic = []

    for element in list(root.iter()):
        if local_name(element.tag) not in SME_TYPES:
            continue

        idshort = get_idshort(element)
        semantic_id = find_direct_child(element, "semanticId")

        if semantic_id is None:
            no_semantic.append(element)
            continue

        value = get_reference_value(semantic_id)

        if value and value in cd_ids:
            continue

        if idshort and idshort in cd_by_idshort:
            target = cd_by_idshort[idshort]
            set_reference(semantic_id, target)
            logs.append({
                "구분": "SME ↔ CD SemanticID",
                "요소": idshort,
                "수정 전": value or "없음",
                "수정 후": target,
                "결과": "Auto Fixed",
            })
            continue

        orphan_groups.setdefault(value or "", []).append((element, idshort))

    for element in no_semantic:
        idshort = get_idshort(element)
        if idshort and idshort in cd_by_idshort:
            target = cd_by_idshort[idshort]
            create_semantic_id(element, target)
            logs.append({
                "구분": "Missing SemanticID",
                "요소": idshort,
                "수정 전": "없음",
                "수정 후": target,
                "결과": "Auto Fixed",
            })
        else:
            orphan_groups.setdefault(f"__NOSEM__{idshort}", []).append(
                (element, idshort)
            )

    if not create_missing:
        for value, members in orphan_groups.items():
            logs.append({
                "구분": "SemanticID 미매칭",
                "요소": members[0][1] or "(idShort 없음)",
                "수정 전": value,
                "수정 후": f"대응 CD 없음 ({len(members)}개 요소)",
                "결과": "Warning",
            })
        return

    for value, members in orphan_groups.items():
        idshorts = [name for _, name in members if name]
        base_name = None

        if idshorts:
            base_name = sanitize_idshort(idshorts[0])
        if not base_name and not value.startswith("__NOSEM__"):
            base_name = name_from_semantic_id(value)
        if not base_name:
            base_name = "GeneratedConcept"

        name = unique_cd_name(base_name, used_names)

        element_type = local_name(members[0][0].tag)
        datatype = SME_DEFAULT_DATATYPE.get(element_type)
        if element_type == "property":
            value_type = text_of(
                find_direct_child(members[0][0], "valueType")
            )
            datatype = preferred_cd_datatype(value_type, False) or "STRING"

        new_id = build_cd_url(name, DEFAULT_VERSION, DEFAULT_REVISION)
        create_concept_description(root, name, new_id, datatype, used_names)
        cd_ids.add(new_id)
        cd_by_idshort.setdefault(name, new_id)

        for element, _ in members:
            semantic_id = find_direct_child(element, "semanticId")
            if semantic_id is None:
                create_semantic_id(element, new_id)
            else:
                set_reference(semantic_id, new_id)

        logs.append({
            "구분": "CD 신규 생성",
            "요소": name,
            "수정 전": (
                "semanticId 없음"
                if value.startswith("__NOSEM__")
                else value
            ),
            "수정 후": f"{new_id} ({len(members)}개 요소 연결)",
            "결과": "Auto Fixed",
        })


# =========================================================
# Property valueType / value
# =========================================================

def split_tokens(idshort):
    text = re.sub(r"[_\-\s]+", " ", idshort or "")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return [t.lower() for t in text.split() if t]


DATETIME_TOKENS = {"datetime", "timestamp"}
DATETIME_CONTEXT = {"start", "end", "failure", "event", "issue", "expiry"}

# ---------------------------------------------------------
# 타입 판정은 "head word"(복합명사의 마지막 단어)를 우선으로 본다.
# 영어 복합명사는 맨 뒤 단어가 그 항목의 정체를 결정한다.
#   TensionControlMethod -> Method  -> 문자열
#   TargetTemperature    -> Temperature -> 실수
#   MaximumTurnCount     -> Count   -> 정수
# "of" 가 있으면 그 앞부분이 본체다. (YearOfConstruction -> Year)
# ---------------------------------------------------------

# 문자열이어야 하는 head
STRING_HEADS = {
    # 식별자·코드
    "id", "ids", "identifier", "number", "no", "code", "serial",
    "version", "revision", "barcode", "batch", "lot", "sku", "gtin",
    "mac", "ip", "key", "password", "checksum", "hash", "signature",
    "uuid", "guid", "reference", "path", "filename", "extension",
    # 이름·설명
    "name", "text", "description", "comment", "note", "label", "title",
    "designation", "remark", "reason", "message", "caption", "summary",
    # 분류·상태
    "type", "kind", "class", "category", "grade", "shape", "material",
    "color", "colour", "state", "status", "mode", "method", "action",
    "priority", "role", "function", "protocol", "encoding",
    "standard", "specification", "system", "format", "unit", "symbol",
    "component", "recipe", "domain", "organization", "department",
    "source", "subject", "scope", "phase", "stage",
    # 주소·연락처
    "address", "street", "city", "country", "region", "zip", "postal",
    "phone", "fax", "email", "contact", "language", "currency",
    "location", "site", "plant", "line", "area",
    # 기타
    "year", "declaration", "certificate", "approval", "guideline",
}

# 참/거짓 head
BOOLEAN_HEADS = {
    "flag", "enabled", "disabled", "detected", "available", "active",
    "valid", "installed", "connected", "required", "present", "yesno",
    "primary", "used", "ok", "pass", "fail",
}

# 정수 head (개수·횟수)
INTEGER_HEADS = {
    "count", "quantity", "qty", "retry", "retries", "cycles", "attempts",
    "errors", "warnings", "alarms", "channels", "slots", "pieces",
    "units", "shots", "passes", "iterations", "turns", "layers",
    "occurrences", "hits", "faults",
}

# 실수 head (측정값·물리량·지표)
REAL_HEADS = {
    # 물리량
    "temperature", "temp", "pressure", "speed", "velocity", "current",
    "voltage", "power", "frequency", "flow", "flowrate", "length",
    "width", "height", "diameter", "radius", "distance", "thickness",
    "depth", "weight", "mass", "volume", "torque", "load", "force",
    "energy", "consumption", "angle", "amplitude", "humidity",
    "density", "viscosity", "resistance", "impedance", "inductance",
    "capacitance", "flux", "luminance", "noise", "wavelength",
    "intensity", "gain", "altitude", "acceleration", "vibration",
    "tension", "pitch", "feed", "stroke", "clearance", "gap",
    "backlash", "runout", "roughness", "hardness", "conductivity",
    "capacity", "throughput", "bandwidth", "latency", "rpm",
    # 비율·정확도·지표
    "accuracy", "precision", "tolerance", "deviation", "error",
    "ratio", "rate", "efficiency", "score", "factor", "index",
    "probability", "percentage", "percent", "offset", "variance",
    "resolution", "calibration", "coordinate", "position", "setpoint",
    "threshold", "limit", "margin", "span", "wear", "degradation",
    "lifetime", "rul", "mtbf", "mttr", "utilization",
    "latitude", "longitude", "elevation", "delta", "difference",
    "average", "mean", "median", "sum", "total",
}

# head 가 애매할 때 숫자임을 시사하는 접두어
NUMERIC_PREFIXES = {
    "minimum", "maximum", "min", "max", "average", "avg", "mean",
    "total", "sum", "rated", "nominal", "peak", "median", "actual",
    "measured", "calculated", "estimated", "remaining", "elapsed",
}

BOOLEAN_PREFIXES = ("is", "has", "can", "was", "does", "are", "should")

URI_HEADS = {"uri", "url", "link", "href", "endpoint", "website"}

STATUS_TOKENS = {"status", "state", "mode"}
RESULT_TOKENS = {"result", "judgement", "judgment", "verdict"}

DECIMAL_PATTERN = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
INTEGER_PATTERN = re.compile(r"[+-]?\d+")


def looks_like_padded_number(text):
    """0044 처럼 앞자리 0이 있는 값은 문자열로 봐야 한다."""
    stripped = text.lstrip("+-")
    return len(stripped) > 1 and stripped.startswith("0") and "." not in stripped


# 그 자체로는 정체를 알려주지 않는 수식어성 head.
# 이런 단어가 끝에 오면 한 칸 앞 단어를 본다.
#   TurnCountTarget -> Count -> 정수
#   AssessmentTarget -> Assessment -> (미확정)
MODIFIER_HEADS = {
    "target", "value", "setting", "spec", "actual", "measured",
    "new", "old", "previous", "next",
}


def head_tokens(idshort):
    """
    idShort 를 토큰으로 쪼개고, 'of' 앞부분을 본체로 삼는다.
    수식어성 단어가 끝에 오면 한 칸 앞으로 넘어간다.
    반환: (전체 토큰 리스트, head 단어)
    """
    tokens = split_tokens(idshort)
    if not tokens:
        return [], ""

    core = tokens
    for separator in ("of", "for", "in", "at", "per"):
        if separator in tokens:
            before = tokens[: tokens.index(separator)]
            if before:
                core = before
                break

    index = len(core) - 1
    while index > 0 and core[index] in MODIFIER_HEADS:
        index -= 1

    return tokens, core[index]


def infer_value_type(idshort, value_text=None):
    """
    idShort 의 head word 와 실제 값에서 xsd valueType 을 추론한다.

    판정 순서
      1. Is/Has 로 시작하면 참/거짓
      2. head word (복합명사의 마지막 단어)
      3. 실제 값의 형태
      4. 전체 토큰 / 숫자 접두어
    """
    tokens, head = head_tokens(idshort)
    token_set = set(tokens)
    text = (value_text or "").strip()

    # 1. Is/Has/Can 접두, 또는 값이 명확히 true/false
    if tokens and tokens[0] in BOOLEAN_PREFIXES and len(tokens) > 1:
        return "xs:boolean"
    if text.lower() in {"true", "false"}:
        return "xs:boolean"

    # 2. head word 기반
    if head in STRING_HEADS:
        return "xs:string"
    if head in BOOLEAN_HEADS:
        return "xs:boolean"
    if head in URI_HEADS:
        return "xs:anyURI"

    if head in DATETIME_TOKENS:
        return "xs:dateTime"
    if head == "date":
        return "xs:dateTime" if token_set & {"datetime"} else "xs:date"
    if head == "time":
        # StartTime/EndTime 은 시각, ResponseTime/CycleTime 은 소요 시간
        if token_set & DATETIME_CONTEXT:
            return "xs:dateTime"
        return "xs:float"

    if head in INTEGER_HEADS:
        return "xs:int"
    if head in REAL_HEADS:
        return "xs:float"

    # 3. 실제 값의 형태
    if text:
        if looks_like_padded_number(text):
            return "xs:string"
        if text.lower() in {"true", "false"}:
            return "xs:boolean"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return "xs:date"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.]+.*", text):
            return "xs:dateTime"
        if INTEGER_PATTERN.fullmatch(text):
            return "xs:float" if tokens and tokens[0] in NUMERIC_PREFIXES \
                else "xs:int"
        if DECIMAL_PATTERN.fullmatch(text):
            return "xs:float"
        return "xs:string"

    # 4. 전체 토큰 / 숫자 접두어
    if token_set & BOOLEAN_HEADS:
        return "xs:boolean"
    if token_set & DATETIME_TOKENS:
        return "xs:dateTime"
    if "date" in token_set:
        return "xs:date"
    if token_set & INTEGER_HEADS:
        return "xs:int"
    if token_set & REAL_HEADS:
        return "xs:float"
    if tokens and tokens[0] in NUMERIC_PREFIXES:
        return "xs:float"

    return "xs:string"


def infer_property_type_and_value(idshort):
    """valueType 과 채워 넣을 샘플 값을 함께 돌려준다."""
    value_type = infer_value_type(idshort)

    if value_type == "xs:string":
        token_set = set(split_tokens(idshort))
        if token_set & STATUS_TOKENS:
            return "xs:string", "Normal"
        if token_set & RESULT_TOKENS:
            return "xs:string", "Success"
        return "xs:string", "Sample"

    return value_type, sample_value_for(value_type)


def normalize_value_type(value_type):
    if not value_type:
        return ""
    return value_type.replace(
        "http://www.w3.org/2001/XMLSchema#", "xs:"
    ).strip()


STRING_TYPES = {"xs:string", "string", "xs:normalizedString"}
URI_TYPES = {"xs:anyURI", "anyURI"}
REAL_TYPES = {
    "xs:float", "xs:double", "xs:decimal", "float", "double", "decimal",
}
INTEGER_TYPES = {
    "xs:int", "xs:integer", "xs:long", "xs:short", "xs:byte",
    "xs:unsignedInt", "xs:unsignedLong", "xs:unsignedShort",
    "xs:unsignedByte", "xs:nonNegativeInteger", "xs:positiveInteger",
    "xs:negativeInteger", "xs:nonPositiveInteger",
    "int", "integer", "long", "short",
}
BOOLEAN_TYPES = {"xs:boolean", "boolean"}


SAMPLE_BY_TYPE = {
    "xs:dateTime": SAMPLE_DATETIME,
    "xs:date": SAMPLE_DATE,
    "xs:time": SAMPLE_TIME,
    "xs:boolean": "true",
    "xs:anyURI": "https://www.smart-factory.kr/",
}


def sample_value_for(value_type, fallback="Sample"):
    normalized = normalize_value_type(value_type)
    if normalized in SAMPLE_BY_TYPE:
        return SAMPLE_BY_TYPE[normalized]
    if normalized in REAL_TYPES:
        return "1.0"
    if normalized in INTEGER_TYPES:
        return "1"
    return fallback


def value_matches_type(value_text, value_type):
    """빈 값은 항상 호환으로 본다(별도 단계에서 채운다)."""
    if value_text is None or not value_text.strip():
        return True

    normalized = normalize_value_type(value_type)
    text = value_text.strip()

    if normalized in BOOLEAN_TYPES:
        return text.lower() in {"true", "false", "1", "0"}
    if normalized in INTEGER_TYPES:
        return re.fullmatch(r"[+-]?\d+", text) is not None
    if normalized in REAL_TYPES:
        return re.fullmatch(
            r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text
        ) is not None
    if normalized == "xs:date":
        return re.fullmatch(
            r"\d{4}-\d{2}-\d{2}(Z|[+-]\d{2}:\d{2})?", text
        ) is not None
    if normalized == "xs:dateTime":
        return re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})?", text
        ) is not None
    if normalized == "xs:time":
        return re.fullmatch(
            r"\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?", text
        ) is not None
    return True


def infer_type_from_value(value_text):
    """실제 값의 형태에서 xsd 타입을 추론한다."""
    if value_text is None:
        return None
    text = value_text.strip()
    if not text:
        return None

    if text.lower() in {"true", "false"}:
        return "xs:boolean"
    if re.fullmatch(r"[+-]?\d+", text):
        return "xs:int"
    if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text):
        return "xs:float"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(Z|[+-]\d{2}:\d{2})?", text):
        return "xs:date"
    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})?", text
    ):
        return "xs:dateTime"
    return "xs:string"


def normalize_value_text(value_text, value_type):
    """xs:boolean 은 소문자여야 한다."""
    if value_text is None:
        return None
    normalized = normalize_value_type(value_type)
    if normalized in BOOLEAN_TYPES:
        lowered = value_text.strip().lower()
        if lowered in {"true", "1"}:
            return "true"
        if lowered in {"false", "0"}:
            return "false"
    return value_text


DOUBLE_ALIASES = {
    "xs:double", "xs:decimal", "double", "decimal",
    "http://www.w3.org/2001/XMLSchema#double",
    "http://www.w3.org/2001/XMLSchema#decimal",
}


def unify_real_value_types(root, logs):
    """실수 타입을 xs:float 하나로 통일한다 (xs:double / xs:decimal 포함)."""
    count = 0
    for element_name in ("property", "range"):
        for element in find_all_by_name(root, element_name):
            value_type_element = find_direct_child(element, "valueType")
            if value_type_element is None:
                continue
            current = text_of(value_type_element)
            if normalize_value_type(current) not in DOUBLE_ALIASES:
                continue
            value_type_element.text = "xs:float"
            count += 1

    # SubmodelElementList 의 valueTypeListElement 도 함께 맞춘다
    for sml in find_all_by_name(root, "submodelElementList"):
        vt_element = find_direct_child(sml, "valueTypeListElement")
        if vt_element is None:
            continue
        if normalize_value_type(text_of(vt_element)) in DOUBLE_ALIASES:
            vt_element.text = "xs:float"
            count += 1

    if count:
        logs.append({
            "구분": "실수 타입 통일",
            "요소": "(전체)",
            "수정 전": "xs:double / xs:decimal",
            "수정 후": f"xs:float ({count}건)",
            "결과": "Auto Fixed",
        })


def refine_property_value_types(root, logs):
    """
    valueType 이 범용 xs:string 으로만 되어 있는 Property 를
    idShort 의 의미와 실제 값을 근거로 구체적인 타입으로 바꾼다.

    - 이미 구체적인 타입(xs:int, xs:float ...)은 건드리지 않는다.
    - SerialNumber, OrderNumber, 0044 처럼 문자열이어야 하는 값은 그대로 둔다.
    - 값을 새로 만들지는 않는다. 기존 값이 있으면 그 형태를 근거로 삼는다.
    """
    for prop in find_all_by_name(root, "property"):
        value_type_element = find_direct_child(prop, "valueType")
        current = normalize_value_type(text_of(value_type_element))

        # 값이 없거나 이미 구체적인 타입이면 여기서는 건드리지 않는다
        if current and current not in STRING_TYPES:
            continue

        idshort = get_idshort(prop)
        if not idshort:
            continue

        value_text = text_of(find_direct_child(prop, "value"))
        inferred = infer_value_type(idshort, value_text)

        if inferred == current or normalize_value_type(inferred) in STRING_TYPES:
            continue

        # 기존 값이 새 타입과 맞지 않으면 바꾸지 않는다 (값 보존 우선)
        if value_text and not value_matches_type(value_text, inferred):
            continue

        if value_type_element is None:
            value_type_element = ET.Element(ns_tag(prop, "valueType"))
            insert_ordered(prop, value_type_element)

        value_type_element.text = inferred
        logs.append({
            "구분": "Property valueType 세분화",
            "요소": idshort,
            "수정 전": f"{current or '없음'} / value={value_text[:20] or '없음'}",
            "수정 후": inferred,
            "결과": "Auto Fixed",
        })

        # 값 표기 정규화 (True -> true)
        value_element = find_direct_child(prop, "value")
        if value_element is not None and value_text:
            normalized = normalize_value_text(value_text, inferred)
            if normalized != value_text:
                value_element.text = normalized
                logs.append({
                    "구분": "Property value 표기 정규화",
                    "요소": idshort,
                    "수정 전": value_text,
                    "수정 후": normalized,
                    "결과": "Auto Fixed",
                })


def fix_missing_property_values(root, logs):
    for prop in find_all_by_name(root, "property"):
        idshort = get_idshort(prop)
        inferred_type, inferred_value = infer_property_type_and_value(idshort)

        value_type = find_direct_child(prop, "valueType")
        if value_type is None:
            value_type = ET.Element(ns_tag(prop, "valueType"))
            insert_ordered(prop, value_type)
            value_type.text = inferred_type
            logs.append({
                "구분": "Property valueType",
                "요소": idshort,
                "수정 전": "없음",
                "수정 후": inferred_type,
                "결과": "Auto Fixed",
            })
        elif not text_of(value_type):
            value_type.text = inferred_type
            logs.append({
                "구분": "Property valueType",
                "요소": idshort,
                "수정 전": "빈 값",
                "수정 후": inferred_type,
                "결과": "Auto Fixed",
            })

        actual_type = text_of(value_type)

        value = find_direct_child(prop, "value")
        if value is None:
            value = ET.Element(ns_tag(prop, "value"))
            insert_ordered(prop, value)
            value.text = sample_value_for(actual_type, inferred_value)
            logs.append({
                "구분": "Property value",
                "요소": idshort,
                "수정 전": "없음",
                "수정 후": value.text,
                "결과": "Auto Fixed",
            })
        elif not text_of(value):
            value.text = sample_value_for(actual_type, inferred_value)
            logs.append({
                "구분": "Property value",
                "요소": idshort,
                "수정 전": "빈 값",
                "수정 후": value.text,
                "결과": "Auto Fixed",
            })


# =========================================================
# CD dataType <-> Property valueType 정렬
# =========================================================

MEASURE_DOWNGRADE = {
    "REAL_MEASURE": "REAL_COUNT",
    "INTEGER_MEASURE": "INTEGER_COUNT",
    "RATIONAL_MEASURE": "RATIONAL",
}

CD_DATATYPE_TO_XSD = {
    "STRING": "xs:string",
    "STRING_TRANSLATABLE": "xs:string",
    "IRDI": "xs:string",
    "IRI": "xs:anyURI",
    "HTML": "xs:string",
    "FILE": "xs:string",
    "BLOB": "xs:string",
    "RATIONAL": "xs:string",
    "RATIONAL_MEASURE": "xs:string",
    "REAL_MEASURE": "xs:float",
    "REAL_COUNT": "xs:float",
    "REAL_CURRENCY": "xs:float",
    "INTEGER_COUNT": "xs:int",
    "INTEGER_MEASURE": "xs:int",
    "INTEGER_CURRENCY": "xs:int",
    "BOOLEAN": "xs:boolean",
    "DATE": "xs:date",
    "TIMESTAMP": "xs:dateTime",
    "TIME": "xs:time",
}


def compatible_cd_datatypes(property_value_type):
    value_type = normalize_value_type(property_value_type)

    # KOSMO Validator 가 실제로 보고한 "매칭 가능한 dataType" 목록에 맞춘다.
    if value_type in URI_TYPES:
        return {"IRI"}
    if value_type in STRING_TYPES:
        return {"STRING", "STRING_TRANSLATABLE", "IRDI", "HTML"}
    if value_type in REAL_TYPES:
        return {"REAL_MEASURE", "REAL_COUNT", "REAL_CURRENCY"}
    if value_type in INTEGER_TYPES:
        return {"INTEGER_COUNT", "INTEGER_MEASURE", "INTEGER_CURRENCY"}
    if value_type in BOOLEAN_TYPES:
        return {"BOOLEAN"}
    if value_type in {"xs:date", "date"}:
        return {"DATE"}
    if value_type in {"xs:dateTime", "dateTime"}:
        return {"TIMESTAMP"}
    if value_type in {"xs:time", "time"}:
        return {"TIME"}
    return None


def preferred_cd_datatype(property_value_type, unit_present):
    value_type = normalize_value_type(property_value_type)

    if value_type in URI_TYPES:
        return "IRI"
    if value_type in STRING_TYPES:
        return "STRING"
    if value_type in REAL_TYPES:
        return "REAL_MEASURE" if unit_present else "REAL_COUNT"
    if value_type in INTEGER_TYPES:
        return "INTEGER_MEASURE" if unit_present else "INTEGER_COUNT"
    if value_type in BOOLEAN_TYPES:
        return "BOOLEAN"
    if value_type in {"xs:date", "date"}:
        return "DATE"
    if value_type in {"xs:dateTime", "dateTime"}:
        return "TIMESTAMP"
    if value_type in {"xs:time", "time"}:
        return "TIME"
    return None


def get_iec61360_content(cd):
    for element in cd.iter():
        if local_name(element.tag) == "dataSpecificationIec61360":
            return element
    return None


def has_unit(cd):
    content = get_iec61360_content(cd)
    if content is None:
        return False
    if text_of(find_direct_child(content, "unit")):
        return True
    unit_id = find_direct_child(content, "unitId")
    return unit_id is not None and bool(get_reference_value(unit_id))


TYPED_ELEMENTS = ("property", "range")


def ensure_iec61360_content(cd):
    """IEC61360 content 가 없으면 골격을 만들어 반환한다."""
    content = get_iec61360_content(cd)
    if content is not None:
        return content
    idshort = get_idshort(cd)
    if not idshort:
        return None
    return build_iec61360(cd, idshort, None)


def align_property_and_cd_types(root, logs, refine_by_value=True):
    """
    Property/Range 의 valueType 과 CD 의 dataType 을 맞춘다.

    1) 선언된 valueType 이 구체적인데 실제 value 가 그 형식이 아니면
       value 를 근거로 valueType 을 다시 정한다. (예: xs:float + "1500 x 3000")
    2) valueType 이 xs:string(범용) 이고 CD 의 dataType 이 더 구체적이며
       value 가 그 형식에 맞으면 valueType 을 끌어올린다.
       (예: xs:string + REAL_MEASURE + "125.5" -> xs:float)
    3) 그 외에는 CD 의 dataType 을 valueType 에 맞춘다.
       CD 에 IEC61360 이나 dataType 이 없으면 새로 만든다.
    """
    cd_by_id = {
        get_id(cd): cd
        for cd in get_concept_descriptions(root)
        if get_id(cd)
    }

    for element_name in TYPED_ELEMENTS:
        for element in find_all_by_name(root, element_name):
            cd = cd_by_id.get(get_semantic_id_value(element))
            if cd is None:
                continue

            value_type_element = find_direct_child(element, "valueType")
            if value_type_element is None:
                continue

            property_type = text_of(value_type_element)
            if not property_type:
                continue

            idshort = get_idshort(element) or f"({element_name})"
            value_element = find_direct_child(element, "value")
            value_text = text_of(value_element)

            content = get_iec61360_content(cd)
            datatype_element = (
                find_direct_child(content, "dataType")
                if content is not None else None
            )
            current_datatype = text_of(datatype_element)

            # --- 1) 값이 선언된 타입과 안 맞으면 값 기준으로 재추론 ---
            if (
                refine_by_value
                and value_text
                and not value_matches_type(value_text, property_type)
            ):
                inferred = infer_type_from_value(value_text)
                if inferred and inferred != property_type:
                    value_type_element.text = inferred
                    logs.append({
                        "구분": "Property valueType (값 기준 재추론)",
                        "요소": idshort,
                        "수정 전": f"{property_type} / value={value_text[:30]}",
                        "수정 후": inferred,
                        "결과": "Auto Fixed",
                    })
                    property_type = inferred

            compatible = compatible_cd_datatypes(property_type)
            if compatible is None:
                continue

            if current_datatype and current_datatype in compatible:
                continue

            # --- 2) xs:string 은 범용이므로 CD 쪽이 구체적이면 끌어올린다 ---
            target_type = CD_DATATYPE_TO_XSD.get(current_datatype)
            if (
                normalize_value_type(property_type) in STRING_TYPES
                and target_type
                and normalize_value_type(target_type) not in STRING_TYPES
                and value_matches_type(value_text, target_type)
            ):
                value_type_element.text = target_type
                logs.append({
                    "구분": "Property valueType (CD 기준 상향)",
                    "요소": idshort,
                    "수정 전": f"{property_type} vs CD {current_datatype}",
                    "수정 후": target_type,
                    "결과": "Auto Fixed",
                })

                if value_element is not None and value_text:
                    normalized = normalize_value_text(value_text, target_type)
                    if normalized != value_text:
                        value_element.text = normalized
                        logs.append({
                            "구분": "Property value 표기 정규화",
                            "요소": idshort,
                            "수정 전": value_text,
                            "수정 후": normalized,
                            "결과": "Auto Fixed",
                        })
                continue

            # --- 3) CD 의 dataType 을 valueType 에 맞춘다 ---
            if content is None:
                content = ensure_iec61360_content(cd)
                if content is None:
                    logs.append({
                        "구분": "CD dataType",
                        "요소": get_idshort(cd) or "(idShort 없음)",
                        "수정 전": "IEC61360 없음",
                        "수정 후": "생성 불가 - 수동 확인 필요",
                        "결과": "Warning",
                    })
                    continue
                datatype_element = find_direct_child(content, "dataType")

            preferred = preferred_cd_datatype(property_type, has_unit(cd))
            if preferred is None:
                continue

            if datatype_element is None:
                datatype_element = ET.Element(ns_tag(content, "dataType"))
                insert_ordered(content, datatype_element)

            datatype_element.text = preferred
            logs.append({
                "구분": "CD dataType (valueType 기준 정렬)",
                "요소": get_idshort(cd),
                "수정 전": current_datatype or "없음",
                "수정 후": preferred,
                "결과": "Auto Fixed",
            })


# IEC61360 에서 존재하면 1자 이상이어야 하는 선택 요소
IEC61360_OPTIONAL_STRINGS = {
    "unit", "sourceOfDefinition", "symbol", "valueFormat", "value",
}

# 존재하면 1자 이상이어야 하는 일반 선택 요소
OPTIONAL_STRINGS = {
    "category", "contentType", "messageTopic", "version", "revision",
    "valueFormat", "symbol", "sourceOfDefinition", "unit",
}

# 자식이 하나도 없으면 지워야 하는 컨테이너
EMPTY_CONTAINERS = {
    "description", "displayName", "preferredName", "shortName",
    "definition", "supplementalSemanticIds", "qualifiers",
    "embeddedDataSpecifications", "specificAssetIds", "extensions",
    "isCaseOf", "keys",
}


def fix_empty_strings(root, logs):
    """
    IDTA 2-14 "String is shorter than 1 characters" 대응.

    - 값이 비어 있는 선택 문자열 요소를 제거한다.
    - text 가 빈 langString 항목을 제거한다.
    - 자식이 없는 컨테이너를 제거한다.
    """
    removed = 0

    for parent in list(root.iter()):
        parent_name = local_name(parent.tag)

        for child in list(parent):
            name = local_name(child.tag)

            # 1) 빈 선택 문자열
            if len(list(child)) == 0 and not text_of(child):
                in_iec = parent_name == "dataSpecificationIec61360"
                if (
                    (in_iec and name in IEC61360_OPTIONAL_STRINGS)
                    or (not in_iec and name in OPTIONAL_STRINGS)
                ):
                    parent.remove(child)
                    removed += 1
                    logs.append({
                        "구분": "빈 문자열 제거 (IDTA 2-14)",
                        "요소": get_idshort(parent) or parent_name,
                        "수정 전": f"<{name}/> (빈 값)",
                        "수정 후": "요소 제거",
                        "결과": "Auto Fixed",
                    })
                    continue

            # 2) text 가 빈 langString
            if name.startswith("langString"):
                text_element = find_direct_child(child, "text")
                if text_element is not None and not text_of(text_element):
                    parent.remove(child)
                    removed += 1
                    continue

    # 3) 남은 빈 컨테이너 정리 (2회 반복해 중첩 케이스도 처리)
    for _ in range(2):
        for parent in list(root.iter()):
            for child in list(parent):
                name = local_name(child.tag)
                if (
                    name in EMPTY_CONTAINERS
                    and len(list(child)) == 0
                    and not text_of(child)
                ):
                    parent.remove(child)
                    removed += 1

    if removed:
        logs.append({
            "구분": "빈 요소 정리 (IDTA 2-14)",
            "요소": "(전체)",
            "수정 전": "빈 문자열/컨테이너",
            "수정 후": f"{removed}건 제거",
            "결과": "Auto Fixed",
        })


def fix_measure_without_unit(root, logs):
    """AASc-3a-009: MEASURE 계열은 unit 또는 unitId 가 필수."""
    for cd in get_concept_descriptions(root):
        content = get_iec61360_content(cd)
        if content is None:
            continue

        datatype_element = find_direct_child(content, "dataType")
        current = text_of(datatype_element)
        if current not in MEASURE_DOWNGRADE:
            continue
        if has_unit(cd):
            continue

        datatype_element.text = MEASURE_DOWNGRADE[current]
        logs.append({
            "구분": "CD dataType (AASc-3a-009)",
            "요소": get_idshort(cd),
            "수정 전": f"{current} (unit 없음)",
            "수정 후": MEASURE_DOWNGRADE[current],
            "결과": "Auto Fixed",
        })


# =========================================================
# SubmodelElementList
# =========================================================

SME_TYPE_NAME = {
    "property": "Property",
    "multiLanguageProperty": "MultiLanguageProperty",
    "range": "Range",
    "file": "File",
    "blob": "Blob",
    "referenceElement": "ReferenceElement",
    "relationshipElement": "RelationshipElement",
    "annotatedRelationshipElement": "AnnotatedRelationshipElement",
    "entity": "Entity",
    "operation": "Operation",
    "capability": "Capability",
    "basicEventElement": "BasicEventElement",
    "submodelElementCollection": "SubmodelElementCollection",
    "submodelElementList": "SubmodelElementList",
}


def get_sml_direct_elements(sml):
    container = find_direct_child(sml, "value")
    if container is None:
        return []
    return [c for c in container if local_name(c.tag) in SME_TYPES]


def fix_sml_child_idshorts(root, logs):
    """AASd-120: SML 의 직접 자식은 idShort 를 가질 수 없다."""
    for sml in find_all_by_name(root, "submodelElementList"):
        sml_idshort = get_idshort(sml)
        for child in get_sml_direct_elements(sml):
            idshort_element = find_direct_child(child, "idShort")
            if idshort_element is None:
                continue
            removed = idshort_element.text
            child.remove(idshort_element)
            logs.append({
                "구분": "SML 자식 idShort (AASd-120)",
                "요소": sml_idshort,
                "수정 전": removed or "",
                "수정 후": "제거",
                "결과": "Auto Fixed",
            })


def fix_sml_constraints(root, logs):
    for sml in find_all_by_name(root, "submodelElementList"):
        idshort = get_idshort(sml)
        children = get_sml_direct_elements(sml)

        child_types = [
            t for t in dict.fromkeys(
                SME_TYPE_NAME.get(local_name(c.tag)) for c in children
            ) if t
        ]

        type_element = find_direct_child(sml, "typeValueListElement")

        if len(child_types) == 1:
            expected = child_types[0]
            if type_element is None:
                type_element = ET.Element(ns_tag(sml, "typeValueListElement"))
                insert_ordered(sml, type_element)
                type_element.text = expected
                logs.append({
                    "구분": "SML typeValueListElement",
                    "요소": idshort,
                    "수정 전": "없음",
                    "수정 후": expected,
                    "결과": "Auto Fixed",
                })
            elif text_of(type_element) != expected:
                old = text_of(type_element)
                type_element.text = expected
                logs.append({
                    "구분": "SML typeValueListElement",
                    "요소": idshort,
                    "수정 전": old or "빈 값",
                    "수정 후": expected,
                    "결과": "Auto Fixed",
                })
        elif len(child_types) > 1:
            logs.append({
                "구분": "SML typeValueListElement (AASd-108)",
                "요소": idshort,
                "수정 전": " / ".join(child_types),
                "수정 후": "자식 타입 불일치 - 수동 확인 필요",
                "결과": "Warning",
            })
        elif type_element is None:
            logs.append({
                "구분": "SML typeValueListElement",
                "요소": idshort,
                "수정 전": "없음 (자식 없음)",
                "수정 후": "추론 불가 - 수동 입력 필요",
                "결과": "Warning",
            })

        if not children:
            continue

        if child_types and child_types[0] in {"Property", "Range"}:
            value_types = [
                v for v in dict.fromkeys(
                    text_of(find_direct_child(c, "valueType"))
                    for c in children
                ) if v
            ]
            if len(value_types) == 1:
                expected = value_types[0]
                vt_element = find_direct_child(sml, "valueTypeListElement")
                if vt_element is None:
                    vt_element = ET.Element(
                        ns_tag(sml, "valueTypeListElement")
                    )
                    insert_ordered(sml, vt_element)
                    vt_element.text = expected
                    logs.append({
                        "구분": "SML valueTypeListElement",
                        "요소": idshort,
                        "수정 전": "없음",
                        "수정 후": expected,
                        "결과": "Auto Fixed",
                    })
                elif text_of(vt_element) != expected:
                    old = text_of(vt_element)
                    vt_element.text = expected
                    logs.append({
                        "구분": "SML valueTypeListElement",
                        "요소": idshort,
                        "수정 전": old or "빈 값",
                        "수정 후": expected,
                        "결과": "Auto Fixed",
                    })
            elif len(value_types) > 1:
                logs.append({
                    "구분": "SML valueTypeListElement (AASd-109)",
                    "요소": idshort,
                    "수정 전": " / ".join(value_types),
                    "수정 후": "자식 valueType 불일치 - 수동 확인 필요",
                    "결과": "Warning",
                })

        semantic_values = [
            v for v in dict.fromkeys(
                get_semantic_id_value(c) for c in children
            ) if v
        ]

        list_element = find_direct_child(sml, "semanticIdListElement")

        if len(semantic_values) > 1:
            if list_element is not None:
                sml.remove(list_element)
                logs.append({
                    "구분": "SML semanticIdListElement (AASd-107)",
                    "요소": idshort,
                    "수정 전": " / ".join(semantic_values[:3]) + " ...",
                    "수정 후": "자식 불일치 - semanticIdListElement 제거",
                    "결과": "Auto Fixed",
                })
            continue

        if len(semantic_values) == 1 and list_element is not None:
            expected = semantic_values[0]
            old = get_reference_value(list_element)
            if old != expected:
                set_reference(list_element, expected)
                logs.append({
                    "구분": "SML semanticIdListElement",
                    "요소": idshort,
                    "수정 전": old or "없음",
                    "수정 후": expected,
                    "결과": "Auto Fixed",
                })


# =========================================================
# CD description / preferredName
# =========================================================

def has_english_lang_string(container):
    if container is None:
        return False
    for lang_string in container:
        language = text_of(find_direct_child(lang_string, "language"))
        text = text_of(find_direct_child(lang_string, "text"))
        if language.lower().startswith("en") and text:
            return True
    return False


SPECIAL_DESCRIPTIONS = {
    "EnergyEfficiency": "Information describing energy efficiency.",
    "CurrentPowerMeasurement":
        "Information describing the current power measurement.",
    "ActualData": "Information describing actual operating data.",
    "KPI": "Information describing a key performance indicator.",
}


def description_text_for(idshort):
    return SPECIAL_DESCRIPTIONS.get(
        idshort, f"Information describing {idshort}."
    )


def preferred_name_for(idshort):
    tokens = split_tokens(idshort)
    if not tokens:
        return idshort
    return " ".join(t.capitalize() for t in tokens)


def add_lang_string(container, lang_string_tag, text_value, language="en"):
    lang_string = ET.SubElement(container, ns_tag(container, lang_string_tag))
    language_element = ET.SubElement(
        lang_string, ns_tag(lang_string, "language")
    )
    language_element.text = language
    text_element = ET.SubElement(lang_string, ns_tag(lang_string, "text"))
    text_element.text = text_value
    return lang_string


def build_iec61360(cd, name, datatype=None):
    container = find_direct_child(cd, "embeddedDataSpecifications")
    if container is None:
        container = ET.Element(ns_tag(cd, "embeddedDataSpecifications"))
        insert_ordered(cd, container)

    embedded = ET.SubElement(
        container, ns_tag(container, "embeddedDataSpecification")
    )

    data_spec = ET.SubElement(embedded, ns_tag(embedded, "dataSpecification"))
    set_reference(data_spec, IEC61360_TEMPLATE_ID)

    wrapper = ET.SubElement(
        embedded, ns_tag(embedded, "dataSpecificationContent")
    )
    content = ET.SubElement(
        wrapper, ns_tag(wrapper, "dataSpecificationIec61360")
    )

    preferred_name = ET.SubElement(content, ns_tag(content, "preferredName"))
    add_lang_string(
        preferred_name,
        "langStringPreferredNameTypeIec61360",
        preferred_name_for(name),
    )

    if datatype:
        datatype_element = ET.SubElement(content, ns_tag(content, "dataType"))
        datatype_element.text = datatype

    definition = ET.SubElement(content, ns_tag(content, "definition"))
    add_lang_string(
        definition,
        "langStringDefinitionTypeIec61360",
        description_text_for(name),
    )
    return content


def ensure_cd_description(cd, logs):
    description = find_direct_child(cd, "description")
    if has_english_lang_string(description):
        return

    idshort = get_idshort(cd)
    if not idshort:
        return

    if description is None:
        description = ET.Element(ns_tag(cd, "description"))
        insert_ordered(cd, description)

    text_value = description_text_for(idshort)
    add_lang_string(description, "langStringTextType", text_value)
    logs.append({
        "구분": "CD Description",
        "요소": idshort,
        "수정 전": "영문 description 없음",
        "수정 후": text_value,
        "결과": "Auto Fixed",
    })


def ensure_cd_preferred_name(cd, logs):
    content = get_iec61360_content(cd)
    if content is None:
        return

    preferred_name = find_direct_child(content, "preferredName")
    if has_english_lang_string(preferred_name):
        return

    idshort = get_idshort(cd)
    if preferred_name is None:
        preferred_name = ET.Element(ns_tag(content, "preferredName"))
        insert_ordered(content, preferred_name)

    text_value = preferred_name_for(idshort)
    add_lang_string(
        preferred_name, "langStringPreferredNameTypeIec61360", text_value
    )
    logs.append({
        "구분": "CD preferredName (IEC61360)",
        "요소": idshort,
        "수정 전": "없음",
        "수정 후": text_value,
        "결과": "Auto Fixed",
    })


def fix_concept_description_content(root, logs, create_iec61360=False):
    for cd in get_concept_descriptions(root):
        if create_iec61360 and get_iec61360_content(cd) is None:
            idshort = get_idshort(cd)
            if idshort:
                build_iec61360(cd, idshort, "STRING")
                logs.append({
                    "구분": "CD IEC61360 생성",
                    "요소": idshort,
                    "수정 전": "없음",
                    "수정 후": "preferredName / dataType=STRING",
                    "결과": "Auto Fixed",
                })
        ensure_cd_description(cd, logs)
        ensure_cd_preferred_name(cd, logs)


# =========================================================
# 파이프라인
# =========================================================

def is_aas_environment(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return False
    return local_name(root.tag) == "environment"


def collect_namespaces(xml_bytes):
    namespace_map = {}
    try:
        for _, item in ET.iterparse(
            io.BytesIO(xml_bytes), events=("start-ns",)
        ):
            prefix, uri = item
            namespace_map.setdefault(prefix, uri)
    except ET.ParseError:
        pass
    return namespace_map


def process_aas_xml(xml_bytes, xml_path, options):
    namespace_map = collect_namespaces(xml_bytes)
    backup = dict(getattr(ET, "_namespace_map", {}))

    try:
        for prefix, uri in namespace_map.items():
            try:
                ET.register_namespace(prefix, uri)
            except (ValueError, TypeError):
                pass

        root = ET.fromstring(xml_bytes)
        logs = []

        # 1. idShort / displayName
        clean_idshorts(root, logs)
        clean_display_names(root, logs)

        # 2. AAS administration + ID
        aas_map = fix_aas_administration(root, logs)
        remap_all_keys(root, aas_map, logs, "AAS ID")

        # 3. Submodel (ID 재작성 전에 소유 관계 캡처)
        owner_map = build_submodel_owner_map(root)
        default_equipment = get_default_equipment_name(root, xml_path)
        sm_map, idshort_map = fix_submodels(
            root, owner_map, default_equipment, logs
        )
        remap_all_keys(root, sm_map, logs, "Submodel ID")
        normalize_aas_submodel_references(root, idshort_map, logs)

        # 4. ConceptDescription ID 정규화 + 참조 리맵 (v2 핵심)
        cd_map = normalize_concept_descriptions(root, logs)
        remap_all_keys(root, cd_map, logs, "ConceptDescription ID")
        remove_duplicate_concept_descriptions(root, logs)

        # 5. semanticId 해소 (매칭 실패 시 CD 생성)
        resolve_semantic_ids(
            root, logs, options.get("create_missing_cd", True)
        )

        # 6. Property valueType 세분화 -> 값 보충
        if options.get("refine_value_types", True):
            refine_property_value_types(root, logs)
        unify_real_value_types(root, logs)
        fix_missing_property_values(root, logs)

        # 7. 타입 정렬
        align_property_and_cd_types(
            root, logs, options.get("refine_by_value", True)
        )
        fix_measure_without_unit(root, logs)

        # 8. SubmodelElementList
        fix_sml_child_idshorts(root, logs)
        fix_sml_constraints(root, logs)

        # 9. CD description / preferredName
        fix_concept_description_content(
            root, logs, options.get("create_iec61360", False)
        )

        # 9-1. 빈 문자열 / 빈 컨테이너 정리 (IDTA 2-14)
        fix_empty_strings(root, logs)

        # 10. 요소 순서 정규화
        if options.get("reorder", True):
            reorder_all(root, logs)

        equipment_names = [
            remove_underscore(get_idshort(s))
            for s in find_all_by_name(root, "assetAdministrationShell")
            if get_idshort(s)
        ]

        output = io.BytesIO()
        ET.ElementTree(root).write(
            output, encoding="utf-8", xml_declaration=True
        )
        return output.getvalue(), logs, equipment_names

    finally:
        ET._namespace_map.clear()
        ET._namespace_map.update(backup)


def process_aasx(file_bytes, options):
    input_buffer = io.BytesIO(file_bytes)
    output_buffer = io.BytesIO()

    logs = []
    processed_files = []
    skipped_json = []
    equipment_names = []
    xml_texts = {}

    with zipfile.ZipFile(input_buffer, "r") as input_zip:
        with zipfile.ZipFile(
            output_buffer, "w", zipfile.ZIP_DEFLATED
        ) as output_zip:
            for item in input_zip.infolist():
                data = input_zip.read(item.filename)
                lower = item.filename.lower()

                if (
                    lower.endswith(".json")
                    and b'"assetAdministrationShells"' in data
                ):
                    skipped_json.append(item.filename)
                    output_zip.writestr(item, data)
                    continue

                if lower.endswith(".xml") and is_aas_environment(data):
                    fixed, file_logs, names = process_aas_xml(
                        data, item.filename, options
                    )
                    output_zip.writestr(item, fixed)
                    logs.extend(file_logs)
                    processed_files.append(item.filename)
                    equipment_names.extend(names)
                    xml_texts[item.filename] = fixed
                else:
                    output_zip.writestr(item, data)

    return {
        "bytes": output_buffer.getvalue(),
        "logs": logs,
        "processed_files": processed_files,
        "skipped_json": skipped_json,
        "equipment_names": list(dict.fromkeys(equipment_names)),
        "xml_texts": xml_texts,
    }


# =========================================================
# 자체 점검
# =========================================================

def self_check(xml_bytes):
    """수정 결과를 KOSMO 규칙 관점에서 다시 훑는다."""
    root = ET.fromstring(xml_bytes)
    problems = []

    cd_ids = {
        get_id(cd) for cd in get_concept_descriptions(root) if get_id(cd)
    }

    for element in root.iter():
        if local_name(element.tag) not in SME_TYPES:
            continue
        semantic_id = find_direct_child(element, "semanticId")
        if semantic_id is None:
            problems.append({
                "유형": "semanticId 없음",
                "요소": f"{local_name(element.tag)} / {get_idshort(element)}",
                "내용": "",
            })
            continue
        value = get_reference_value(semantic_id)
        if value not in cd_ids:
            problems.append({
                "유형": "CD 미매칭",
                "요소": f"{local_name(element.tag)} / {get_idshort(element)}",
                "내용": value or "",
            })

    for cd in get_concept_descriptions(root):
        content = get_iec61360_content(cd)
        if content is None:
            continue
        datatype = text_of(find_direct_child(content, "dataType"))
        if datatype in MEASURE_DOWNGRADE and not has_unit(cd):
            problems.append({
                "유형": "AASc-3a-009",
                "요소": get_idshort(cd),
                "내용": f"{datatype} 인데 unit 없음",
            })
        if not has_english_lang_string(
            find_direct_child(content, "preferredName")
        ):
            problems.append({
                "유형": "preferredName 없음",
                "요소": get_idshort(cd),
                "내용": "",
            })

    for element in root.iter():
        idshort = get_idshort(element)
        if idshort and not IDSHORT_PATTERN.match(idshort):
            problems.append({
                "유형": "idShort 규칙",
                "요소": f"{local_name(element.tag)} / {idshort}",
                "내용": "",
            })

    cd_by_id = {
        get_id(cd): cd for cd in get_concept_descriptions(root) if get_id(cd)
    }
    for element_name in TYPED_ELEMENTS:
        for element in find_all_by_name(root, element_name):
            cd = cd_by_id.get(get_semantic_id_value(element))
            if cd is None:
                continue

            value_type = text_of(find_direct_child(element, "valueType"))
            compatible = compatible_cd_datatypes(value_type)
            if compatible is None:
                continue

            content = get_iec61360_content(cd)
            datatype = (
                text_of(find_direct_child(content, "dataType"))
                if content is not None else ""
            )

            if not datatype:
                problems.append({
                    "유형": "CD dataType 없음",
                    "요소": get_idshort(element) or element_name,
                    "내용": f"{value_type} / CD {get_idshort(cd)}",
                })
            elif datatype not in compatible:
                problems.append({
                    "유형": "valueType ↔ dataType",
                    "요소": get_idshort(element) or element_name,
                    "내용": f"{value_type} vs {datatype}",
                })

            value_text = text_of(find_direct_child(element, "value"))
            if value_text and not value_matches_type(value_text, value_type):
                problems.append({
                    "유형": "value ↔ valueType",
                    "요소": get_idshort(element) or element_name,
                    "내용": f"{value_text[:30]} / {value_type}",
                })

    # AAS ID 형식
    for shell in find_all_by_name(root, "assetAdministrationShell"):
        idshort = get_idshort(shell)
        expected = build_aas_url(idshort, *determine_version_revision(shell))
        if get_id(shell) != expected:
            problems.append({
                "유형": "AAS ID 형식",
                "요소": idshort,
                "내용": f"{get_id(shell)} != {expected}",
            })

    # 빈 문자열 (IDTA 2-14)
    for element in root.iter():
        name = local_name(element.tag)
        if len(list(element)) == 0 and element.text is not None:
            if not element.text.strip() and name not in {
                "orderRelevant", "keys", "value",
            }:
                problems.append({
                    "유형": "빈 문자열 (IDTA 2-14)",
                    "요소": name,
                    "내용": "",
                })

    return problems


def verify_with_aas_core(xml_bytes):
    try:
        import aas_core3.xmlization as xmlization
        import aas_core3.verification as verification
    except ImportError:
        return None

    try:
        environment = xmlization.environment_from_str(
            xml_bytes.decode("utf-8")
        )
    except Exception as error:  # noqa: BLE001
        return [{"경로": "(역직렬화 실패)", "오류": str(error)}]

    problems = []
    for issue in verification.verify(environment):
        problems.append({"경로": str(issue.path), "오류": issue.cause})
        if len(problems) >= 300:
            break
    return problems


# =========================================================
# Streamlit UI
# =========================================================

st.title("🔧 AAS Validator Auto Fixer")

st.write(
    "AASX의 정상 데이터는 유지하면서 KOSMO Validator와 AAS 3.0 표준 제약에서 "
    "반복적으로 걸리는 항목을 자동 보정합니다."
)

with st.expander("📋 적용되는 Auto Fix 규칙"):
    st.markdown(
        """
**ConceptDescription ID와 참조의 동기화**
CD의 `id`를 `/ids/cd/{CD명}/{version}/{revision}` 으로 바꾼 뒤,
그 id를 참조하던 문서 전체의 key value를 함께 갱신합니다.
`idShort`가 없는 SubmodelElementList 자식이나, idShort가 CD명과 다른 요소의
`semanticId`도 끊어지지 않습니다.

**매칭되지 않는 semanticId**
어떤 CD와도 매칭되지 않으면 같은 `idShort`의 CD로 연결하고,
그것도 없으면 ConceptDescription을 새로 만들어 연결합니다.
`semanticId`가 아예 없는 요소도 같은 방식으로 처리합니다.

**Submodel**
`id`와 `semanticId`를
`/ids/sm/{AAS idShort}/{SM idShort}/{version}/{revision}` 으로 통일하고,
AAS의 참조를 `ModelReference` / key type `Submodel` 로 맞춥니다.
AAS가 여러 개면 Submodel별 소유 AAS를 따로 판별합니다.

**AAS ID**
`/ids/aas/{AAS idShort}/{version}/{revision}` 형식으로 맞추고,
그 id를 참조하던 곳도 함께 갱신합니다.

**valueType ↔ dataType**
세 단계로 맞춥니다.
값이 선언된 타입과 안 맞으면 값 기준으로 `valueType`을 다시 정하고
(`xs:float` + `1500 × 3000` → `xs:string`),
`valueType`이 범용 `xs:string`인데 CD가 더 구체적이면 끌어올리고
(`xs:string` + `REAL_MEASURE` + `125.5` → `xs:float`),
그 외에는 CD의 `dataType`을 `valueType`에 맞춥니다
(`xs:date` → `DATE`, `xs:anyURI` → `IRI`).
CD에 IEC61360이나 `dataType`이 아예 없으면 만들어 넣습니다.
`xs:boolean` 값은 소문자로 정규화합니다(`True` → `true`).

**IDTA 2-14 문자열 최소 길이**
`<value/>` 처럼 비어 있는 선택 문자열 요소와 빈 컨테이너를 제거합니다.

**AASc-3a-009**
`REAL_MEASURE` / `INTEGER_MEASURE` 인데 `unit`·`unitId`가 없으면
`REAL_COUNT` / `INTEGER_COUNT` 로 낮춥니다.

**idShort**
언더바 제거에 더해 KOSMO 규칙(대문자 시작, 2자 이상, 하이픈으로 끝나지 않음)에
맞게 보정합니다.

**SubmodelElementList**
AASd-120 자식 `idShort` 제거, AASd-108 `typeValueListElement`,
AASd-109 `valueTypeListElement`, AASd-107 `semanticIdListElement` 처리.

**요소 순서**
AAS 3.0 XSD는 전부 `xs:sequence` 이므로 마지막에 전체 자식 순서를 재정렬합니다.

**기타**
누락 version/revision 보충 · 영문 description 보충 ·
IEC61360 `preferredName` 보충 · 완전 동일 중복 CD 제거
        """
    )

with st.expander("⚙️ 옵션"):
    create_missing_cd = st.checkbox(
        "매칭되는 CD가 없으면 ConceptDescription 자동 생성",
        value=True,
        help="끄면 Warning으로만 보고합니다.",
    )
    refine_value_types = st.checkbox(
        "xs:string으로만 되어 있는 valueType을 세분화",
        value=True,
        help=(
            "idShort 의미와 실제 값을 근거로 xs:int / xs:float / "
            "xs:boolean / xs:date 등으로 나눕니다. 실수는 xs:float 으로 "
            "통일하며, SerialNumber, 0044 처럼 "
            "문자열이어야 하는 값은 그대로 둡니다."
        ),
    )
    refine_by_value = st.checkbox(
        "값이 형식에 안 맞으면 valueType을 값 기준으로 재추론",
        value=True,
        help=(
            "예: valueType이 xs:float인데 값이 '1500 × 3000' 이면 "
            "xs:string으로 바꿉니다."
        ),
    )
    create_iec61360_option = st.checkbox(
        "IEC61360 데이터 스펙이 없는 CD에 골격 자동 생성",
        value=False,
    )
    reorder_option = st.checkbox(
        "요소 순서 재정렬 (XSD sequence 준수)", value=True
    )
    run_self_check = st.checkbox("수정 후 자체 점검 실행", value=True)

st.divider()

uploaded_file = st.file_uploader(
    "수정할 원본 AASX 파일을 업로드하세요.", type=["aasx"]
)

if uploaded_file is None:
    st.info("이전 AutoFixed 파일이 아니라 수정 전 원본 AASX를 넣어주세요.")
else:
    col1, col2 = st.columns(2)
    col1.metric("파일명", uploaded_file.name)
    col2.metric("파일 크기", f"{uploaded_file.size / 1024:.1f} KB")

    if st.button("🔧 Auto Fix 실행", type="primary", use_container_width=True):
        try:
            uploaded_file.seek(0)
            original_bytes = uploaded_file.read()

            options = {
                "create_missing_cd": create_missing_cd,
                "refine_value_types": refine_value_types,
                "refine_by_value": refine_by_value,
                "create_iec61360": create_iec61360_option,
                "reorder": reorder_option,
            }

            result = process_aasx(original_bytes, options)

            if not result["processed_files"]:
                st.error(
                    "AASX 안에서 AAS Environment XML을 찾지 못했습니다. "
                    "루트 태그가 `environment` 인 XML이 있어야 합니다."
                )
                if result["skipped_json"]:
                    st.warning(
                        "JSON 형식 AAS 파일: "
                        + ", ".join(result["skipped_json"])
                        + " — 이 도구는 XML 직렬화만 지원합니다."
                    )
            else:
                st.success("Auto Fix가 완료되었습니다.")
                st.caption(
                    "처리한 XML: " + ", ".join(result["processed_files"])
                )

                if result["equipment_names"]:
                    st.subheader("🏭 인식한 AAS(장비)")
                    for name in result["equipment_names"]:
                        st.code(name)

                logs = result["logs"]
                fixed_count = sum(
                    1 for log in logs if log["결과"] == "Auto Fixed"
                )
                warning_count = sum(
                    1 for log in logs if log["결과"] == "Warning"
                )

                col1, col2, col3 = st.columns(3)
                col1.metric("처리 항목", len(logs))
                col2.metric("Auto Fixed", fixed_count)
                col3.metric("Warning", warning_count)

                st.divider()
                st.subheader("📋 수정 내역")
                if logs:
                    st.dataframe(
                        logs, use_container_width=True, hide_index=True
                    )
                else:
                    st.info("현재 규칙으로 수정할 항목이 없습니다.")

                if run_self_check:
                    st.divider()
                    st.subheader("🔍 자체 점검")
                    for name, xml_bytes in result["xml_texts"].items():
                        problems = self_check(xml_bytes)
                        if problems:
                            st.warning(f"{name}: {len(problems)}건 남음")
                            st.dataframe(
                                problems,
                                use_container_width=True,
                                hide_index=True,
                            )
                        else:
                            st.success(f"{name}: 점검 항목 모두 통과")

                        core_problems = verify_with_aas_core(xml_bytes)
                        if core_problems is None:
                            st.caption(
                                "`pip install aas-core3.0` 을 설치하면 "
                                "AAS 3.0 표준 제약까지 함께 검사합니다."
                            )
                        elif core_problems:
                            st.warning(
                                f"{name}: 표준 제약 {len(core_problems)}건"
                            )
                            st.dataframe(
                                core_problems,
                                use_container_width=True,
                                hide_index=True,
                            )
                        else:
                            st.success(f"{name}: 표준 제약 위반 없음")

                base_name = uploaded_file.name.rsplit(".", 1)[0]
                st.divider()
                st.download_button(
                    label="⬇️ 수정된 AASX 다운로드",
                    data=result["bytes"],
                    file_name=f"{base_name}_AutoFixed.aasx",
                    mime="application/octet-stream",
                    type="primary",
                    use_container_width=True,
                )

        except zipfile.BadZipFile:
            st.error("올바른 AASX(ZIP) 파일이 아닙니다.")
        except Exception as error:  # noqa: BLE001
            st.error("AASX 처리 중 오류가 발생했습니다.")
            st.exception(error)
