# 하드코딩 상수 분류 리포트 — DB 이동 후보 스캔

> 대상: `app.py`, `src/*.py`
> 목적: 모듈 레벨의 "문자열 리터럴로 구성된 tuple/list/dict" 상수를 찾아, DB로 옮길 후보인지 분류
> **이 리포트는 조사만 함. 마이그레이션 코드/스키마 변경은 하지 않음.**

## 분류 기준 (이번 대화에서 세운 원칙 그대로)

| 카테고리 | 정의 |
|---|---|
| **[DB 이동 후보]** | 프로그램이 값을 단순 조회 / 멤버십 체크(`x in text`, 매칭)에만 쓰는 **순수 참조 데이터**. LLM 프롬프트에 텍스트로 직접 들어가지 않음. |
| **[코드 유지 - 프롬프트/템플릿]** | LLM system/user 메시지에 텍스트로 **직접 삽입**되는 지시문·문구·템플릿. |
| **[코드 유지 - 운영 파라미터]** | 숫자 임계값·모델명·타임아웃·재시도 횟수 등 튜닝값, 또는 **코드 구조에 1:1로 종속된 내부 식별자**. |

## 스캔 범위

`grep '^[A-Z_]... ='` 로 `app.py` + `src/` 9개 파일의 모듈 레벨 대문자/`_` 상수를 전부 확인.
그중 **tuple/list/dict + 문자열 리터럴** 조건에 맞는 것은 아래 9개.
(SQL 쿼리 문자열 `SCHEMA_STATEMENTS`, CSS 블록 `CARD_CSS`, 스칼라 상수, 인라인 정규식은 제외 — "제외 항목" 절 참고.)

---

## 리포트 표

| 파일 | 변수명 | 타입 | 카테고리 | 판단 이유 | `keyword_lists` 재사용 가능 여부 |
|---|---|---|---|---|---|
| `src/blog_agent.py` | `_LOCAL_KEYWORDS` | `tuple[str]` (~45개) | **[DB 이동 후보]** | `needs_local_context()` 에서 `any(kw in text for kw in _LOCAL_KEYWORDS)` 멤버십 체크에만 사용. LLM에 텍스트로 안 감. `local_keywords` 패턴 그 자체. | ✅ `(list_name='local_keywords', value=<키워드>)` 2컬럼으로 충분. 순서 무관·중복 없음. |
| `src/blog_agent.py` | `_REGION_NAMES` | `tuple[str]` (~34개) | **[DB 이동 후보]** | `has_region_name()` 에서 `any(name in text for name in _REGION_NAMES)` 멤버십 체크. LLM에 안 감. `region_names` 패턴. | ✅ `(list_name='region_names', value=<지역명>)` 2컬럼으로 충분. |
| `src/blog_agent.py` | `_CONTRAST_MARKERS` | `tuple[str]` (18개) | **[DB 이동 후보]** | `check_contrast_repetition()` 에서 `any(marker in sentence for marker in _CONTRAST_MARKERS)` 멤버십 체크. LLM에 안 감. `_LOCAL_KEYWORDS` 와 완전히 같은 패턴(이번 대화에서 새로 추가됨). | ✅ `(list_name='contrast_markers', value=<표현>)` 2컬럼으로 충분. 현재 조사 결합형("와 달리"/"과 달리")을 모두 나열한 형태 → 그대로 행으로 넣으면 됨. |
| `src/db.py` | `TRUSTED_SOURCE_SEEDS` | `list[tuple[str,str,str]]` (4개) | **[DB 이동 후보]** (이미 부분적으로 해당) | `init_db()` 가 `trusted_sources` 테이블에 `INSERT` 하는 **시드 데이터**. `search_tools.match_trusted()` 가 domain 멤버십 체크에 사용, LLM에 안 감. Python 리터럴은 시드 소스일 뿐 — 사실상 이미 "DB 데이터". | ❌ `(list_name, value)` 2컬럼으로는 부족 (`domain`·`name`·`category` 3필드). **이미 존재하는 `trusted_sources` 테이블이 정확히 그 스키마** → `keyword_lists` 아님. 남는 판단은 "시드를 코드에 둘지 vs SQL/CSV로 뺄지". |
| `src/blog_agent.py` | `TONE_INSTRUCTIONS` | `dict[str,str]` (4개) | [코드 유지 - 프롬프트/템플릿] | 값(한국어 지시문 문단)이 `write_draft` / `critique_draft` / `rewrite_draft` 의 `system` 프롬프트에 **문자열로 그대로 concat**. | — |
| `app.py` | `TONE_OPTIONS` | `list[str]` (4개) | [코드 유지 - 프롬프트/템플릿] | `st.selectbox` 옵션. **`TONE_INSTRUCTIONS` 의 키 집합과 반드시 일치**해야 함(선택값이 `TONE_INSTRUCTIONS.get()` 으로 매핑됨). 프롬프트 텍스트(위)와 한 덩어리라 분리 불가 — 사실상 `TONE_INSTRUCTIONS` 의 파생. | — |
| `src/search_tools.py` | `TOOLS` | `list[dict]` (1개) | [코드 유지 - 프롬프트/템플릿] | web_search 도구 스키마. `description` / `input_schema.description` 문자열이 **Anthropic API tool 정의**로 요청에 실려 모델이 읽음(언제/어떻게 도구를 호출할지 지시) = 사실상 프롬프트. 구조도 API 스키마라 DB 대상 아님. | — |
| `app.py` | `RESULT_KEYS` | `tuple[str]` (18개) | [코드 유지 - 운영 파라미터] | `st.session_state` 에서 pop/set 할 **키 이름 목록**. `generate_blog` 결과 dict 키와 1:1. 조회는 하지만 도메인 참조 데이터가 아니라 코드 구조 산출물. DB로 옮기면 코드-DB 강결합만 늘어남. | — |
| `app.py` | `_STAGE_LABELS` | `dict[str, tuple[str,str]]` (9개) | [코드 유지 - 운영 파라미터] (경계선) | `_STAGE_LABELS[stage]` 룩업 방식은 [DB 이동 후보]와 기계적으로 비슷하고 LLM에 안 감. **그러나** 값이 도메인 참조 데이터가 아니라 UI 진행 문구(microcopy)이고, 키가 `blog_agent._emit` 의 단계명과 강결합. 옮긴다면 i18n/UI 문자열 관리 성격 — 이번 기준의 "참조 데이터"는 아님. | — |

---

## [DB 이동 후보] 요약

옮길 만한 것: **`_LOCAL_KEYWORDS`, `_REGION_NAMES`, `_CONTRAST_MARKERS`** (+ `TRUSTED_SOURCE_SEEDS` 는 이미 `trusted_sources` 테이블 소관).

### `keyword_lists` 테이블 관련 사실

- **현재 스키마(`src/db.py` `SCHEMA_STATEMENTS`)에 `keyword_lists` 테이블은 없음.** `trusted_sources`, `blog_posts`, `search_cache` 3개뿐.
- 앞의 3개 리스트는 전부 "단순 문자열 목록"이라 **한 테이블에 `(list_name, value)` 형태로 공존 가능**:
  - `list_name='local_keywords'` → 45행
  - `list_name='region_names'` → 34행
  - `list_name='contrast_markers'` → 18행
- 최소 스키마 예상(참고용, 생성하지 않음): `keyword_lists(list_name TEXT, value TEXT, PRIMARY KEY (list_name, value))`. 운영상 `sort_order INT`, `active BOOLEAN DEFAULT true` 정도 추가 여지.
- `TRUSTED_SOURCE_SEEDS` 는 `domain`/`name`/`category` 3필드라 2컬럼 `keyword_lists` 에 안 맞음 → 별도 테이블이 필요하고, 그 테이블(`trusted_sources`)은 **이미 존재**함. 즉 "새 테이블" 논의 대상 아님.

### 옮길 때 고려할 점 (참고, 실행 아님)

- 셋 다 요청마다 자주 조회됨(`needs_local_context`/`has_region_name` 는 검색어마다, `check_contrast_repetition` 는 재작성 때). `search_tools._trusted_sources()` 처럼 **프로세스 1회 캐시(`lru_cache`)** 필요.
- DB 미가동 시 폴백 필요 — `get_trusted_sources()` 가 실패 시 빈 리스트를 주듯이, 이 리스트들도 빈 목록이면 "지역화 안 함 / 반복 감지 안 함"으로 degrade 되어야 함(하드 실패 금지).
- `_CONTRAST_MARKERS` 는 조사 결합형을 전부 나열한 상태 — DB에 그대로 넣거나, 어간만 저장하고 조사는 코드에서 결합하는 정규화도 선택지(스키마에는 영향 없음).

---

## 제외 항목 (스캔은 했으나 조건 밖)

| 파일 | 이름 | 제외 사유 |
|---|---|---|
| `src/db.py` | `SCHEMA_STATEMENTS` | SQL 쿼리 문자열 리스트 (명시적 제외 대상). |
| `app.py` | `CARD_CSS` | 스칼라 문자열(CSS 블록). tuple/list/dict 아님. |
| `src/config.py` | `MODEL`, `SIMILARITY_THRESHOLD`, `SEARCH_CACHE_SIMILARITY`, `SEARCH_CACHE_TTL_HOURS`, `DEFAULT_REGION`, `PEXELS_API_KEY`, `PEXELS_SEARCH_URL` | 스칼라. (참고: 이들이 "운영 파라미터" 카테고리의 전형 — `MODEL`·임계값·TTL. 이미 `config.py` 로 모여 있음.) |
| `src/blog_agent.py` | `DEFAULT_TONE`, `REGION_GUIDELINE`, `_LOW_GROUNDING_INSTRUCTION` | 스칼라. `REGION_GUIDELINE`·`_LOW_GROUNDING_INSTRUCTION` 은 프롬프트 텍스트(카테고리상 [코드 유지 - 프롬프트/템플릿]), `DEFAULT_TONE` 은 기본값. |
| `src/search_tools.py` | `_RETRY_SYSTEM`, `_GROUNDING_SYSTEM` | 스칼라 프롬프트 문자열. |
| `src/images.py` | `_KEYWORD_SYSTEM`, `_HTTP_TIMEOUT` | 스칼라. 프롬프트 / 타임아웃. |
| `src/research_agent.py` | `SYSTEM_PROMPT` | 스칼라 프롬프트 문자열. |
| `src/embeddings.py` | `MODEL_NAME`, `EMBEDDING_DIM`, `_model` | 스칼라 (모델명 / 차원 / 런타임 캐시). |
| (여러 파일) | 정규식 패턴 | 모듈 레벨 상수로 뽑힌 정규식 없음 — `extract_json`, `_split_sentences`, `slugify`, `_card_image_url`, `_plain_excerpt` 등 **함수 내부 인라인**. (명시적 제외 대상이기도 함.) |

---

## 한 줄 결론

DB로 옮길 실질 후보는 **`_LOCAL_KEYWORDS` · `_REGION_NAMES` · `_CONTRAST_MARKERS`** 3개.
전부 `(list_name, value)` 2컬럼짜리 신규 `keyword_lists` 테이블 하나에 담을 수 있음.
`TRUSTED_SOURCE_SEEDS` 는 이미 `trusted_sources` 테이블이 담당(시드 위치만 논의 대상).
나머지(`TONE_INSTRUCTIONS`, `TOOLS`, `RESULT_KEYS`, `_STAGE_LABELS`, `TONE_OPTIONS`)는 프롬프트 텍스트이거나 코드 구조에 종속되어 코드에 남겨야 함.
