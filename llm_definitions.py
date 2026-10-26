"""
CD 이름/정의 생성기 (Claude Messages API)
=========================================

aas_fixer_core.collect_cd_text_targets() 가 모은 대상(idShort + 사용 맥락)을
Claude API 로 보내 영문/한글 이름과 정의를 받아온다.

* API 키는 호출하는 쪽(app.py)이 st.secrets / 환경변수에서 읽어 넘긴다.
  코드에 키를 적지 않는다.
* 보내는 정보: idShort, 요소 종류, valueType, 단위, 상위/하위 idShort,
  (있으면) 기존 영문/한글 정의, (옵션) 예시값. AAS/Submodel/CD 의 id URL 은
  보내지 않는다.
* 표준 라이브러리(urllib)만 사용한다.
"""

import json
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5-5"
API_VERSION = "2023-06-01"

HANGUL = re.compile(r"[가-힣]")
PLACEHOLDER = re.compile(r"^(\[TBD\]|\[작성 필요\]|Information describing )", re.I)


SYSTEM_PROMPT = """\
You write ConceptDescription texts (IEC 61360 preferredName and definition)
for an Asset Administration Shell (AAS) reference model of manufacturing
equipment used in Korea. Each item is one data element identified by its
idShort, with its element type, valueType, unit and position in the model.

For every item return:
- en_name: English preferred name. Split the CamelCase idShort into words in
  Title Case and spell out abbreviations (e.g. "RUL" -> "Remaining Useful
  Life"). At most 60 characters.
- ko_name: concise Korean technical term used in Korean factories
  (e.g. "권취 속도 설정값"). No English words unless they are standard
  acronyms (PLC, MES, ID, RPM, Lot).
- en_definition: one sentence that states what the data means, written as a
  noun phrase (e.g. "Setpoint of the rotational speed of the winding
  shaft"). No final period.
- ko_definition: the same meaning in natural Korean, ending with a noun
  (e.g. "권취축 회전 속도의 설정값", "~여부", "~정보", "~목록").
  Do not end with "~이다" or "~합니다".

Rules:
- Describe the meaning of the data itself, not the AAS structure.
- SubmodelElementCollection: describe what the group manages, referring to
  its children (e.g. "Information on ... including ...",
  "...을 관리하는 정보").
- SubmodelElementList: describe it as a list of repeated items
  ("List of ...", "...별 ... 목록").
- xs:boolean: "Indicates whether ..." / "... 여부".
- Names ending in Deviation: actual value minus setpoint
  ("실측값 − 설정값") when a setpoint is implied.
- Range elements: "Range of ..." / "... 범위".
- Count/Number of: "Number of ..." / "... 수".
- Use the equipment domain given by the user to choose the right meaning
  (e.g. "Winding" in a battery winding machine means jelly-roll winding).
- If an existing definition is provided, keep its meaning and only
  translate or polish it.
- Items with "standard": true come from IDTA submodel templates or standard
  dictionaries (ECLASS / IEC CDD). For these, copy "given_en_name" into
  en_name and "given_en_definition" into en_definition exactly as given,
  and write ko_name / ko_definition as a faithful Korean translation of the
  given English. Do not add or drop meaning. If given_en_definition is
  empty, write a short definition from the name.
- Never include example values, units, company names, product names or
  invented standard numbers in names or definitions.
- Never use placeholder wording such as "Information describing".

Respond with JSON only, no markdown, in this exact shape:
{"items": [{"idShort": "...", "en_name": "...", "ko_name": "...",
"en_definition": "...", "ko_definition": "..."}]}
"""


class LLMError(Exception):
    pass


def build_user_message(targets, domain):
    payload = {
        "equipment": domain or "manufacturing equipment",
        "items": targets,
    }
    return (
        "Write the texts for these AAS data elements.\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )


def call_messages_api(api_key, model, system, user_message, base_url=None,
                      max_tokens=8000, timeout=120, retries=2):
    """Claude Messages API 호출. 응답 텍스트를 돌려준다."""
    url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/v1/messages"
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }).encode("utf-8")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }

    last_error = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            last_error = LLMError(f"HTTP {error.code}: {detail}")
            # 429(속도 제한), 5xx, 529(과부하)만 재시도
            if error.code not in (429, 500, 502, 503, 504, 529):
                raise last_error from None
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = LLMError(f"연결 실패: {error}")
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise last_error


def parse_items(text):
    """응답에서 JSON 을 꺼낸다 (코드펜스가 섞여 와도 처리)."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("응답에서 JSON 을 찾지 못했습니다.")
    data = json.loads(cleaned[start:end + 1])
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LLMError("응답 JSON 에 items 목록이 없습니다.")
    return items


def validate_item(item):
    """반영해도 되는 결과인지 점검. 문제 문자열 또는 None."""
    for key in ("idShort", "en_name", "ko_name", "en_definition",
                "ko_definition"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            return f"{key} 없음"
    if not HANGUL.search(item["ko_name"]) or not HANGUL.search(
        item["ko_definition"]
    ):
        return "한글 텍스트 아님"
    if PLACEHOLDER.match(item["en_definition"]):
        return "자리표시자 문구"
    if len(item["en_definition"]) > 400 or len(item["ko_definition"]) > 300:
        return "정의가 너무 김"
    return None


def generate_cd_texts(targets, api_key, domain="", model=DEFAULT_MODEL,
                      base_url=None, batch_size=25, cache=None,
                      progress=None):
    """
    targets -> ({idShort: entry}, [오류]) 를 돌려준다.
    cache(dict) 를 넘기면 같은 idShort 는 다시 요청하지 않는다.
    """
    if not api_key:
        return {}, [{"message": "API 키가 설정되지 않았습니다."}]

    cache = cache if cache is not None else {}
    results, errors = {}, []
    pending = []
    for target in targets:
        cached = cache.get(target["idShort"])
        if cached:
            results[target["idShort"]] = cached
        else:
            pending.append(target)

    batches = [
        pending[i:i + batch_size] for i in range(0, len(pending), batch_size)
    ]
    for index, batch in enumerate(batches, start=1):
        if progress:
            progress(index - 1, len(batches))
        try:
            text = call_messages_api(
                api_key, model, SYSTEM_PROMPT,
                build_user_message(batch, domain), base_url=base_url,
            )
            items = parse_items(text)
        except (LLMError, ValueError) as error:
            errors.append({
                "idShort": f"배치 {index} ({len(batch)}개)",
                "message": str(error),
            })
            continue

        wanted = {t["idShort"] for t in batch}
        by_id = {t["idShort"]: t for t in batch}
        returned = set()
        for item in items:
            idshort = (item.get("idShort") or "").strip()
            if idshort not in wanted:
                continue
            problem = validate_item(item)
            if problem:
                errors.append({"idShort": idshort, "message": problem})
                continue
            entry = {
                key: item[key].strip() for key in (
                    "en_name", "ko_name", "en_definition", "ko_definition",
                )
            }
            source = by_id[idshort]
            if source.get("standard"):
                # 표준 CD 영문은 모델 출력과 무관하게 원본을 쓴다
                entry["en_name"] = source.get("given_en_name") or entry[
                    "en_name"
                ]
                if source.get("given_en_definition"):
                    entry["en_definition"] = source["given_en_definition"]
            results[idshort] = entry
            cache[idshort] = entry
            returned.add(idshort)
        for idshort in sorted(wanted - returned - set(
            e["idShort"] for e in errors
        )):
            errors.append({"idShort": idshort, "message": "응답에서 누락"})

    if progress:
        progress(len(batches), len(batches))
    return results, errors


def make_generator(api_key, domain="", model=DEFAULT_MODEL, base_url=None,
                   batch_size=25, cache=None, progress=None):
    """aas_fixer_core 의 options['cd_text_generator'] 에 넣을 함수."""
    def generator(targets):
        return generate_cd_texts(
            targets, api_key, domain=domain, model=model, base_url=base_url,
            batch_size=batch_size, cache=cache, progress=progress,
        )
    return generator
