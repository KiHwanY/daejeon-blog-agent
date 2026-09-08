# 대전 블로그 에이전트 (daejeon-blog-agent)

대전 지역을 기본 맥락으로 삼아 **웹 리서치부터 블로그 초안 작성·자체 검토까지 자동화**하는 Claude 기반 AI 에이전트입니다.
주제를 입력하면 에이전트가 필요한 만큼 웹 검색을 수행하고(tool-use), 그 결과를 정리해
**리서치 → 아웃라인 → 초안 → 자체 검토 → (필요하면) 재작성 → 대표 이미지** 순서로 한국어 블로그 글을 만들어 줍니다.

축제·맛집·행사처럼 지역 정보가 필요한 주제는 검색어와 프롬프트에 자동으로 "대전"을 반영하고, 대전시청·대전관광공사·
지역 언론 등 신뢰할 수 있는 지역 소스를 우선 참고하도록 설계되어 있습니다. 완성된 글은 임베딩 벡터·자체 검토 결과와 함께
PostgreSQL(pgvector)에 저장되며, 다음에 비슷한 주제가 들어오면 새로 생성하지 않고 기존 글을 재사용해
중복 작업과 API 비용을 줄입니다. 웹 검색 결과도 캐시해 재사용합니다. 사용은 Streamlit 웹 UI 또는 CLI로 할 수 있습니다.

## 주요 기능

- **웹 검색 tool-use 에이전트** — Claude API의 tool-use 루프로, 모델이 스스로 판단해 `web_search`(DuckDuckGo) 도구를 호출하고 검색 결과에 근거해 답변. 루프가 최종 정리를 만들지 못하고 끝나면 도구 없이 정리를 한 번 더 요청
- **신뢰 소스 우선순위 검색** — 지역 키워드가 있는데 지역명이 없는 검색어에는 자동으로 "대전"을 추가. `trusted_sources` 테이블에 대전시청·대전관광공사·대전일보·중도일보를 시드로 등록하고, 검색 결과 중 신뢰 도메인은 `[신뢰 소스: ...]` 태그를 달아 상단 정렬 → 리서치 프롬프트가 우선 인용하도록 유도. Streamlit 출처 목록에는 ✅ 배지 표시
- **검색 결과 캐시(`search_cache`)** — 검색어를 임베딩해, 정확히 같거나 코사인 유사도 0.97 이상인 최근(기본 7일) 검색이 있으면 DuckDuckGo 재호출 없이 캐시 결과를 재사용
- **리서치 부족 시 자동 재시도** — 리서치 후 유효 검색 결과가 2건 이하이면, Claude에게 "왜 부족했는지·어떤 키워드로 다시 찾을지" 물어 새 검색어로 **1회만** 재검색 (`search_retried` 로 기록)
- **자체 검토(critique) · 재작성(rewrite)** — 초안 작성 직후 Claude가 사실 정확성·구조·문체(톤)·SEO 4개 기준으로 스스로 평가하고, 미흡하면 피드백을 반영해 **최대 1회** 다시 씀 (`critique_passed` / `critique_feedback` / `was_rewritten` 저장). 자세한 동작은 [아래](#자체-검토--재작성-동작-방식) 참고
- **SEO 키워드 반복 감지** — 재작성본에서 SEO 키워드를 본문 주제와 대비·구별짓는 상투 구문(`~와 달리`, `~와는 무관하게` 등)이 3회 이상 반복되면, LLM 호출 없이 규칙 기반으로 감지해 검토 피드백에 경고를 덧붙임(2차 재작성은 하지 않고 기록만)
- **리서치 → 아웃라인 → 초안 파이프라인** — 단계로 나눠 각 단계 결과를 확인하며 진행, 초안은 출처 링크가 포함된 마크다운으로 생성. 톤앤매너(정보성/캐주얼/리뷰형/전문적)와 SEO 키워드를 선택 가능
- **Pexels 기반 카드 이미지** — 주제를 Claude로 2~4단어 영어 검색어로 바꿔 [Pexels](https://www.pexels.com/api/)에서 대표 사진을 찾아 글과 함께 저장. 키가 없거나 검색이 실패하면 예외 없이 본문 주제 기반 플레이스홀더로 대체. 유사 글 카드는 각자의 이미지를 표시
- **pgvector 기반 유사 글 중복 방지** — 주제를 `ko-sroberta`로 임베딩해 `blog_posts`에서 코사인 거리(`<=>`)로 최근접 글을 조회, 유사도가 임계값(기본 0.85) 이상이면 새로 생성하지 않고 기존 글(과 자체 검토 이력)을 그대로 반환
- **Streamlit UI** — 주제/톤/SEO 입력, "블로그 생성" 버튼, 단계별 진행 표시, "에이전트 판단 로그" 접이식 패널(검색 재시도·자체 검토 결과·재작성 여부·피드백), 출처 링크, 카드/초안 렌더링, `.md` 다운로드
- **Streamlit 에러 처리** — Anthropic 인증·rate limit·연결·HTTP 오류와 DB 연결 오류를 사람이 읽을 수 있는 한국어 안내로 바꿔 표시하고 실행을 안전하게 중단

## 기술 스택

| 분류 | 사용 기술 |
|---|---|
| 언어 | Python 3.10+ (개발/테스트: 3.12) |
| LLM | Claude API (`anthropic` SDK, 모델 `claude-sonnet-5`, tool-use) |
| 웹 검색 | `ddgs` (DuckDuckGo) |
| 이미지 | Pexels API (`GET /v1/search`) |
| HTTP 클라이언트 | `httpx` (Pexels 호출용) |
| 데이터베이스 | PostgreSQL + `pgvector` 확장 (`vector(768)`, 코사인용 **HNSW 인덱스**) |
| 임베딩 | `sentence-transformers` — `jhgan/ko-sroberta-multitask` (768차원, 로컬 실행) |
| 프론트엔드 | Streamlit |
| 인프라 | Docker (PostgreSQL + pgvector 컨테이너) |
| 테스트 / 린트 | `pytest`, `ruff` (E/F/I 규칙) |
| CI | GitHub Actions (`push`(main) · 모든 PR에서 `ruff check` + `pytest`) |
| 기타 | `python-dotenv`, `psycopg2-binary` |

## 폴더 구조

```
daejeon-blog-agent/
├── app.py                  # Streamlit 프론트엔드 (진입점)
├── requirements.txt        # 런타임 의존 패키지 (버전 고정)
├── requirements-dev.txt    # 런타임 + pytest · ruff (개발/CI)
├── pyproject.toml          # pytest · ruff 설정
├── .env.example            # 환경변수 템플릿 — 복사해서 .env 생성
├── .gitignore
├── .github/
│   └── workflows/ci.yml    # push(main)/PR 시 ruff + pytest 실행
├── tests/                  # 단위 테스트 (LLM·DB·네트워크 모두 모의, 91개)
│   ├── conftest.py         # src/ 를 import 경로에 추가
│   ├── test_text_utils.py  # 지역화·SEO 키워드·slug 등 순수 함수
│   ├── test_search_tools.py # 신뢰 소스 판별·검색 루프·재시도·JSON 추출
│   ├── test_images.py      # Pexels 이미지 검색 (Claude·httpx 모의)
│   ├── test_critique.py    # critique_draft · 반복 감지 · rewrite 폴백
│   └── test_generate_blog.py # generate_blog 파이프라인 오케스트레이션
└── src/
    ├── config.py           # 공용 설정 상수 (모델 ID · 임계값 · 캐시 TTL · Pexels 키)
    ├── search_tools.py      # web_search 도구 + tool-use 루프 + 재시도 + JSON 추출 (공용)
    ├── research_agent.py    # 웹 검색 tool-use 리서치 에이전트 (독립 실행형 Q&A)
    ├── blog_agent.py        # 리서치→아웃라인→초안→자체검토→재작성 파이프라인 + 유사 글 중복 방지
    ├── images.py            # Pexels 이미지 검색 (한국어 주제 → 영어 키워드 → URL)
    ├── backfill_images.py   # image_url 이 빈 기존 blog_posts 행을 채우는 스크립트
    ├── db.py                # PostgreSQL/pgvector 연결 · 스키마 초기화 · 유사도/캐시 조회
    └── embeddings.py        # ko-sroberta 로컬 임베딩 (768차원, 전역 캐시)
```

DB 스키마(`src/db.py`가 생성):

| 테이블 | 용도 | 주요 컬럼 |
|---|---|---|
| `trusted_sources` | 신뢰 도메인 목록 (대전 소스 4개 시드) | `domain` (UNIQUE), `name`, `category` |
| `blog_posts` | 생성된 블로그 글 + 임베딩 + 메타 | `topic`, `outline`, `draft`, `final_content`, `tone`, `seo_keywords`, `image_url`, `critique_passed`, `critique_feedback`, `was_rewritten`, `search_retried`, `embedding vector(768)` |
| `search_cache` | 웹 검색 결과 캐시 (검색어 임베딩으로 재사용) | `query`, `results_json`, `embedding vector(768)`, `created_at` |
| `keyword_lists` | 코드에 있던 참조용 문자열 목록(지역 키워드·지역명·대비 표현)을 DB로 이관 | `list_name`, `value` (PK: `list_name`+`value`) — `local_keywords` / `region_names` / `contrast_markers` 3종 시드 |

- `blog_posts.embedding` · `search_cache.embedding` 에는 코사인 거리(`<=>`) 최근접 검색용 **HNSW 인덱스**가 생성되어, 글이 늘어나도 유사도 조회가 순차 스캔으로 느려지지 않습니다.
- `search_cache.created_at` 에는 TTL 필터용 B-tree 인덱스가 있습니다.
- `blog_posts` 의 확장 컬럼(`image_url` · 자체 검토 메타)은 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 로 추가되어, 기존 DB에서 `python src/db.py` 를 다시 실행해도 안전합니다.
- `keyword_lists` 는 `needs_local_context` / `has_region_name` / `check_contrast_repetition` 가 멤버십 체크에만 쓰던 문자열 목록을 옮긴 것입니다. `blog_agent._keyword_list()` 가 프로세스당 1회만 조회해 캐시하고, DB가 없거나 목록이 비면 해당 기능(지역화·반복 감지)을 조용히 끕니다(하드 실패 없음).

## 설치 방법

### 1. 저장소 클론

```bash
git clone https://github.com/KiHwanY/daejeon-blog-agent.git
cd daejeon-blog-agent
```

### 2. 가상환경 생성 및 활성화

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. 패키지 설치

```bash
pip install -r requirements.txt          # 앱 실행만 할 경우
# 또는
pip install -r requirements-dev.txt      # + pytest · ruff (개발/테스트까지)
```

> 최초 실행 시 임베딩 모델(`jhgan/ko-sroberta-multitask`, 약 440MB)이 자동으로 다운로드되어 로컬에 캐시됩니다.
> `streamlit` 등을 여러 파이썬 환경에 설치했다면, 반드시 이 가상환경의 인터프리터로 실행하세요
> (`.venv\Scripts\python -m streamlit run app.py`). 그렇지 않으면 `httpx` 같은 패키지를 못 찾을 수 있습니다.

### 4. Docker로 PostgreSQL + pgvector 실행

```bash
docker run -d \
  --name daejeon-blog-db \
  -e POSTGRES_DB=daejeon_blog \
  -e POSTGRES_USER=bloguser \
  -e POSTGRES_PASSWORD=blogpass \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

`pgvector/pgvector` 이미지는 `vector` 확장을 포함하므로 별도 설치 없이 `CREATE EXTENSION vector`가 동작합니다.
컨테이너의 `POSTGRES_*` 값은 아래 `.env` 값과 반드시 일치해야 합니다.

### 5. 환경변수 설정 (`.env`)

`.env.example`을 복사해 `.env`를 만들고 값을 채웁니다.

```bash
cp .env.example .env
```

```dotenv
# Claude API 키 (https://console.anthropic.com/ 에서 발급)
ANTHROPIC_API_KEY=sk-ant-...

# PostgreSQL + pgvector 접속 정보 (4단계 docker 컨테이너와 일치)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=daejeon_blog
DB_USER=bloguser
DB_PASSWORD=blogpass

# (선택) Pexels 이미지 API 키 (https://www.pexels.com/api/ 무료 발급)
# 없으면 카드 이미지는 플레이스홀더로 대체됩니다.
PEXELS_API_KEY=
```

### 6. DB 테이블 초기화

```bash
python src/db.py
```

`vector` 확장 생성 → `trusted_sources` / `blog_posts` / `search_cache` / `keyword_lists` 테이블·확장 컬럼 생성 → HNSW 인덱스 생성 →
`trusted_sources`(대전 도메인 4개)·`keyword_lists`(지역 키워드·지역명·대비 표현) 시딩 →
생성 결과 검증 출력까지 수행합니다. 멱등이라 여러 번 실행해도 안전합니다.

### 7. (선택) 기존 글 이미지 백필

`image_url` 컬럼을 새로 추가한 뒤, 이미 저장돼 있던 글에 이미지를 채우려면:

```bash
python src/backfill_images.py            # image_url 이 비어 있는 행 전부
python src/backfill_images.py --dry-run  # 실제 UPDATE 없이 검색 결과만 확인
```

`PEXELS_API_KEY` 가 있어야 동작합니다.

## 실행 방법

### Streamlit 웹 UI (권장)

```bash
streamlit run app.py
```

실행 후 브라우저에서 **http://localhost:8501** 로 접속합니다. 주제·톤·SEO 키워드를 입력하고
**블로그 생성**을 누르면, 유사한 기존 글이 있으면 카드로 보여 주고, 없으면
리서치 → 아웃라인 → 초안 → 자체 검토 → (필요 시) 재작성 → 이미지 검색 단계를 차례로 진행한 뒤
결과를 `blog_posts`에 저장합니다. "에이전트 판단 로그" 패널에서 검색 재시도·자체 검토 결과·재작성 여부·피드백을
확인할 수 있고, 완성된 초안은 `.md` 파일로 내려받을 수 있습니다.

> `python app.py`로 실행하면 동작하지 않습니다. 반드시 `streamlit run`을 사용하세요.

### CLI (선택)

```bash
# 블로그 파이프라인 (유사 글 확인 → 리서치 → 아웃라인 → 초안 → 자체 검토 → 저장)
python src/blog_agent.py

# 단순 리서치 Q&A 에이전트
python src/research_agent.py
```

## 자체 검토 / 재작성 동작 방식

초안(`write_draft`)이 나온 직후, `generate_blog` 파이프라인은 다음을 수행합니다.

1. **`critique_draft(draft, research_context, tone, seo_keywords)`** — Claude에게 초안·리서치 근거·요청한 톤·SEO 키워드를 함께 주고, 아래 **네 가지 기준만으로** 평가하게 합니다.
   - **사실 정확성** — 초안의 사실이 리서치 근거와 일치하는가, 근거에 없는 내용을 지어내지 않았는가
   - **구조** — 도입–본문–마무리 흐름이 논리적이고 소제목 구성이 자연스러운가
   - **문체** — 요청한 톤앤매너와 실제 문체가 일치하는가
   - **SEO** — 지정한 키워드가 자연스럽게 반영됐는가 (전혀 없거나 억지로 반복하면 실패)

   응답은 `{"pass": true/false, "feedback": "..."}` JSON으로 받고, 파싱에 실패하면 통과로 처리한 뒤 그 사실을 피드백에 남깁니다.

2. **재작성** — `pass`가 `false`이면 `rewrite_draft`가 피드백을 반영해 초안을 **한 번만** 다시 씁니다(무한 루프 방지). 재작성 프롬프트에는 "이전 초안과 같은 수사·문장 구조를 반복하지 말 것", "SEO 키워드를 본문과 대비·구별짓는 방식으로 반복하지 말고 인트로·본문·결론에 고르게 나눌 것" 지침이 들어갑니다. 재작성 응답이 비면 빈 글 대신 이전 초안을 유지합니다.

3. **규칙 기반 반복 점검** — 재작성이 일어난 경우, `check_contrast_repetition`가 LLM 없이 "`~와 달리`·`~와는 무관하게` 같은 대비 표현 + SEO 키워드"가 같은 문장에 함께 나오는 횟수를 셉니다. 3회 이상이면 `critique_feedback`에 `⚠️[자동 점검] ...` 경고를 덧붙입니다(2차 재작성은 하지 않고 기록만).

4. **저장** — `critique_passed` · `critique_feedback` · `was_rewritten` · `search_retried` 를 `blog_posts` 에 함께 저장합니다. 기존 글을 재사용할 때도 이 값들이 함께 조회되어 Streamlit "에이전트 판단 로그"에 표시됩니다.

관련해서, **리서치 단계**에서도 유효 검색 결과가 2건 이하이면 Claude에게 새 검색어를 물어 1회 재검색하고(`search_retried=True`), tool-use 루프가 최종 정리 텍스트 없이 끝나면 도구 없이 정리를 한 번 더 요청합니다.

## 테스트

```bash
pip install -r requirements-dev.txt

ruff check .    # 린트 + import 정렬 (E/F/I, 한국어 폭 때문에 E501 은 제외)
pytest          # 단위 테스트
```

- `tests/` 에 **91개** 케이스(5개 파일)가 있으며, **Claude API·DuckDuckGo·Pexels·PostgreSQL 호출을 전부 모의(mock)** 처리합니다. API 키나 실행 중인 DB 없이 `pytest`만으로 통과합니다.
- 커버 범위: 지역화/SEO 키워드/slug 등 순수 함수(`test_text_utils`), 신뢰 도메인 판별·tool-use 루프·검색 재시도·JSON 추출(`test_search_tools`), Pexels 이미지 검색의 모든 실패 경로(`test_images`), `critique_draft` JSON 파싱·반복 감지·재작성 폴백(`test_critique`), `generate_blog` 단계 순서와 결과 dict(`test_generate_blog`).
- `pyproject.toml` 에 pytest(`pythonpath=["src"]`)·ruff 설정이 있고, `.github/workflows/ci.yml` 이 `main` push와 모든 PR에서 위 두 명령을 실행합니다.

## 주의사항

- **Anthropic API는 사용량 기반 과금**입니다. "블로그 생성" 한 번에 리서치 tool-use 루프(최대 5턴 + 정리 1회) + 아웃라인 + 초안 + 자체 검토 + (재작성 시) 1회 + 이미지 키워드 변환으로 여러 번의 API 호출이 발생합니다. [console.anthropic.com](https://console.anthropic.com/)에서 사용량과 한도를 확인하고, 반복 테스트 시 비용에 유의하세요. (단위 테스트는 API를 호출하지 않습니다.)
- **`.env` 파일은 절대 커밋하지 마세요.** 실제 API 키와 DB 비밀번호가 들어 있습니다. `.gitignore`에 이미 포함되어 있으며, 커밋할 때는 항상 `git status`로 `.env`가 스테이징되지 않았는지 확인하세요. 실수로 노출되었다면 즉시 해당 API 키를 폐기(rotate)하고 재발급하세요.
- 임베딩 모델은 최초 1회 로컬 다운로드(약 440MB)가 필요하며, 이후에는 캐시에서 즉시 로드됩니다.
- `PEXELS_API_KEY` 는 선택입니다. 없으면 이미지 관련 기능이 자동으로 꺼지고 플레이스홀더가 사용됩니다.

## 라이선스

개인 학습용 프로젝트입니다.
