# 대전 블로그 에이전트 (daejeon-blog-agent)

대전 지역을 기본 맥락으로 삼아 **웹 리서치부터 블로그 초안 작성까지 자동화**하는 Claude 기반 AI 에이전트입니다.
주제를 입력하면 에이전트가 필요한 만큼 웹 검색을 수행하고(tool-use), 그 결과를 정리해 **리서치 → 아웃라인 → 초안**
순서로 한국어 블로그 글을 만들어 줍니다.

축제·맛집·행사처럼 지역 정보가 필요한 주제는 검색어와 프롬프트에 자동으로 "대전"을 반영하고, 대전시청·대전관광공사·
지역 언론 등 신뢰할 수 있는 지역 소스를 우선 참고하도록 설계되어 있습니다. 완성된 글은 임베딩 벡터와 함께
PostgreSQL(pgvector)에 저장되며, 다음에 비슷한 주제가 들어오면 새로 생성하지 않고 기존 글을 재사용해
중복 작업과 API 비용을 줄입니다. 사용은 Streamlit 웹 UI 또는 CLI로 할 수 있습니다.

## 주요 기능

- **웹 검색 tool-use 에이전트** — Claude API의 tool-use 루프로, 모델이 스스로 판단해 `web_search`(DuckDuckGo) 도구를 호출하고 검색 결과에 근거해 답변
- **대전 로컬 소스 우선순위** — 모든 단계 프롬프트에 "사용자는 대전 거주" 맥락을 주입하고, 지역 키워드가 있는데 지역명이 없는 검색어에는 자동으로 "대전"을 추가. `trusted_sources` 테이블에 대전시청·대전관광공사·대전일보·중도일보를 기본 시드로 등록
- **리서치 → 아웃라인 → 초안 파이프라인** — 3단계로 나눠 각 단계 결과를 확인하며 진행, 초안은 출처 링크가 포함된 마크다운으로 생성
- **pgvector 기반 유사 글 중복 방지** — 주제를 `ko-sroberta`로 임베딩해 `blog_posts`에서 코사인 거리(`<=>`)로 최근접 글을 조회, 유사도가 임계값(기본 0.85) 이상이면 기존 글을 그대로 반환
- **Streamlit UI** — 주제 입력창, "블로그 생성" 버튼, 단계별 진행 표시, 출처 링크, 초안 마크다운 렌더링, `.md` 다운로드

## 기술 스택

| 분류 | 사용 기술 |
|---|---|
| 언어 | Python 3.10+ (개발/테스트: 3.12) |
| LLM | Claude API (`anthropic` SDK, 모델 `claude-sonnet-5`, tool-use) |
| 웹 검색 | `ddgs` (DuckDuckGo) |
| 데이터베이스 | PostgreSQL + `pgvector` 확장 (`vector(768)`) |
| 임베딩 | `sentence-transformers` — `jhgan/ko-sroberta-multitask` (768차원, 로컬 실행) |
| 프론트엔드 | Streamlit |
| 인프라 | Docker (PostgreSQL + pgvector 컨테이너) |
| 기타 | `python-dotenv`, `psycopg2-binary` |

## 폴더 구조

```
daejeon-blog-agent/
├── app.py                  # Streamlit 프론트엔드 (진입점)
├── requirements.txt        # 의존 패키지 (버전 고정)
├── .env.example            # 환경변수 템플릿 — 복사해서 .env 생성
├── .gitignore
├── main.py                 # (미사용) PyCharm 기본 생성 파일
└── src/
    ├── research_agent.py   # 웹 검색 tool-use 리서치 에이전트 (독립 실행형 Q&A)
    ├── blog_agent.py       # 리서치 → 아웃라인 → 초안 파이프라인 + 유사 글 중복 방지
    ├── db.py               # PostgreSQL/pgvector 연결 · 스키마 초기화 · 유사도 검색
    └── embeddings.py       # ko-sroberta 로컬 임베딩 (768차원, 전역 캐시)
```

DB 스키마(`src/db.py`가 생성):

| 테이블 | 용도 | 주요 컬럼 |
|---|---|---|
| `trusted_sources` | 신뢰 도메인 목록 (대전 소스 4개 시드) | `domain` (UNIQUE), `name`, `category` |
| `blog_posts` | 생성된 블로그 글 + 임베딩 | `topic`, `outline`, `draft`, `final_content`, `embedding vector(768)` |
| `search_cache` | 검색 결과 캐시용 (예약) | `query`, `results_json`, `embedding vector(768)` |

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
pip install -r requirements.txt
```

> 최초 실행 시 임베딩 모델(`jhgan/ko-sroberta-multitask`, 약 440MB)이 자동으로 다운로드되어 로컬에 캐시됩니다.

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
```

### 6. DB 테이블 초기화

```bash
python src/db.py
```

`vector` 확장 생성 → `trusted_sources` / `blog_posts` / `search_cache` 테이블 생성 → 대전 기본 도메인 4개 시딩 →
생성 결과 검증 출력까지 수행합니다. 멱등이라 여러 번 실행해도 안전합니다.

## 실행 방법

### Streamlit 웹 UI (권장)

```bash
streamlit run app.py
```

실행 후 브라우저에서 **http://localhost:8501** 로 접속합니다. 주제(기본값: `대전 가을 축제`)를 입력하고
**블로그 생성**을 누르면, 유사한 기존 글이 있으면 안내와 함께 그 글을 보여 주고, 없으면 리서치 → 아웃라인 → 초안
단계를 차례로 진행한 뒤 결과를 `blog_posts`에 저장합니다. 완성된 초안은 `.md` 파일로 내려받을 수 있습니다.

> `python app.py`로 실행하면 동작하지 않습니다. 반드시 `streamlit run`을 사용하세요.

### CLI (선택)

```bash
# 블로그 파이프라인 (유사 글 확인 → 리서치 → 아웃라인 → 초안 → 저장)
python src/blog_agent.py

# 단순 리서치 Q&A 에이전트
python src/research_agent.py
```

## 주의사항

- **Anthropic API는 사용량 기반 과금**입니다. "블로그 생성" 한 번에 리서치 tool-use 루프(최대 5턴) + 아웃라인 + 초안으로 여러 번의 API 호출이 발생합니다. [console.anthropic.com](https://console.anthropic.com/)에서 사용량과 한도를 확인하고, 반복 테스트 시 비용에 유의하세요.
- **`.env` 파일은 절대 커밋하지 마세요.** 실제 API 키와 DB 비밀번호가 들어 있습니다. `.gitignore`에 이미 포함되어 있으며, 커밋할 때는 항상 `git status`로 `.env`가 스테이징되지 않았는지 확인하세요. 실수로 노출되었다면 즉시 해당 API 키를 폐기(rotate)하고 재발급하세요.
- 임베딩 모델은 최초 1회 로컬 다운로드(약 440MB)가 필요하며, 이후에는 캐시에서 즉시 로드됩니다.

## 라이선스

개인 학습용 프로젝트입니다.
