"""
대전 지역 기반 블로그 작성 에이전트
- 리서치(web_search tool_use 루프) → 아웃라인 → 초안 3단계 파이프라인

사전 준비:
  pip install -r requirements.txt
  프로젝트 루트 .env 에 ANTHROPIC_API_KEY 설정

단독 실행(CLI):
  python src/blog_agent.py
Streamlit 프론트엔드:
  streamlit run app.py
"""

import os
import re

from anthropic import Anthropic
from ddgs import DDGS
from dotenv import load_dotenv

from db import find_similar_post, save_blog_post
from embeddings import embed_text

# 프로젝트 루트(혹은 상위 경로)의 .env 파일에서 환경변수를 읽어온다.
load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-sonnet-5"

# 유사 글로 간주하는 코사인 유사도 하한
SIMILARITY_THRESHOLD = 0.85

# 사용자의 기본 지역 맥락
DEFAULT_REGION = "대전"

# 모든 단계의 시스템 프롬프트에 포함되는 지역 맥락 지침
REGION_GUIDELINE = (
    "사용자는 대전에 거주 중이며, 지역 정보가 필요한 주제(축제·행사·맛집·날씨·명소·"
    "교통·부동산 등)는 대전을 기본 맥락으로 삼는다. "
    "주제에 다른 지역명이 명시된 경우에는 그 지역을 우선한다."
)

# 지역 맥락이 필요한 주제인지 판단하는 키워드
_LOCAL_KEYWORDS = (
    "축제", "행사", "공연", "전시", "맛집", "카페", "베이커리", "브런치", "날씨",
    "여행", "관광", "명소", "가볼만한", "주말", "나들이", "데이트", "코스", "근교",
    "등산", "산책", "공원", "시장", "재래시장", "불꽃", "벚꽃", "단풍", "야경",
    "핫플", "병원", "부동산", "아파트", "전세", "월세", "분양", "교통", "지하철",
    "버스", "학원", "도서관", "캠핑", "숙소", "호텔", "펜션", "지역", "동네",
)

# 이미 특정 지역명이 들어 있으면 '대전'을 자동으로 덧붙이지 않는다
_REGION_NAMES = (
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "수원", "성남", "용인", "고양", "천안", "청주", "전주", "포항", "창원", "김해",
    "구미", "아산", "당진", "논산", "공주", "세종시",
)


def needs_local_context(text: str) -> bool:
    """지역 정보가 필요한 주제/검색어인지 여부"""
    return any(kw in text for kw in _LOCAL_KEYWORDS)


def has_region_name(text: str) -> bool:
    """문자열에 이미 특정 지역명이 포함되어 있는지 여부"""
    return any(name in text for name in _REGION_NAMES)


def localize_query(query: str, region: str = DEFAULT_REGION) -> str:
    """지역 맥락이 필요한데 지역명이 없으면 region을 앞에 붙여 반환"""
    if needs_local_context(query) and not has_region_name(query):
        return f"{region} {query}".strip()
    return query


# 에이전트가 사용할 도구 정의(스키마)
TOOLS = [
    {
        "name": "web_search",
        "description": (
            "웹에서 최신 정보, 뉴스, 통계, 특정 주제에 대한 사실을 검색합니다. "
            "모르는 내용이나 최신성이 중요한 질문에는 반드시 이 도구를 사용하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색할 키워드. 짧고 구체적으로 작성하세요.",
                }
            },
            "required": ["query"],
        },
    }
]


def _web_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo 웹 검색 실행 → [{title, url, snippet}, ...] 반환"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:  # noqa: BLE001 - 검색 실패는 도구 결과로 전달
        return [{"title": "", "url": "", "snippet": f"검색 중 오류가 발생했습니다: {e}"}]

    return [
        {
            "title": r.get("title") or "",
            "url": r.get("href") or "",
            "snippet": r.get("body") or "",
        }
        for r in results
    ]


def _format_hits(hits: list[dict]) -> str:
    if not hits:
        return "검색 결과가 없습니다."
    lines = []
    for h in hits:
        lines.append(
            f"- 제목: {h['title']}\n  URL: {h['url']}\n  요약: {h['snippet']}"
        )
    return "\n".join(lines)


def _text(response) -> str:
    """응답에서 마지막 텍스트 블록을 뽑아 반환"""
    out = ""
    for block in response.content:
        if block.type == "text" and block.text.strip():
            out = block.text
    return out


def _run_search_loop(
    user_prompt: str,
    system_prompt: str,
    max_turns: int = 5,
    on_search=None,
) -> tuple[str, list[dict]]:
    """tool_use 루프를 돌며 (최종 텍스트, 출처 리스트) 반환

    on_search: 검색이 실행될 때마다 호출되는 콜백 (localized_query: str) -> None
    """
    messages = [{"role": "user", "content": user_prompt}]
    final_text = ""
    sources: list[dict] = []
    seen: set[str] = set()

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        block_text = _text(response)
        if block_text:
            final_text = block_text

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "web_search":
                # 2) 지역명이 필요한 검색어면 '대전'을 자동 포함
                query = localize_query(block.input["query"])
                if on_search:
                    on_search(query)
                else:
                    print(f"[검색] {query}")

                hits = _web_search(query)
                for h in hits:
                    if h["url"] and h["url"] not in seen:
                        seen.add(h["url"])
                        sources.append(h)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _format_hits(hits),
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    return final_text, sources


# --------------------------------------------------------------------------
# 3단계 파이프라인
# --------------------------------------------------------------------------

def research(topic: str, on_search=None) -> tuple[str, list[dict]]:
    """1단계: 주제에 대한 리서치. (리서치 노트, 출처 리스트) 반환"""
    system = (
        "당신은 블로그 글감을 조사하는 리서치 에이전트입니다. "
        + REGION_GUIDELINE
        + " 최신 정보나 사실 확인이 필요하면 반드시 web_search 도구를 사용하세요. "
        "지역 정보가 필요한 주제라면 검색어에 '대전'을 포함하세요. "
        "조사한 내용은 추측 없이 사실 위주로 불릿으로 정리하고, "
        "각 항목 끝에 참고한 출처 URL을 함께 적으세요."
    )
    prompt = f"다음 주제로 블로그 글을 쓰기 위한 리서치를 해주세요: {topic}"
    return _run_search_loop(prompt, system, on_search=on_search)


def make_outline(topic: str, research_notes: str) -> str:
    """2단계: 리서치 노트를 바탕으로 아웃라인 작성"""
    system = "당신은 한국어 블로그 편집자입니다. " + REGION_GUIDELINE
    prompt = (
        f"주제: {topic}\n\n"
        f"리서치 노트:\n{research_notes}\n\n"
        "위 내용을 바탕으로 블로그 글 아웃라인을 작성하세요. "
        "제목 후보 1개, 3~5개의 소제목, 소제목별로 다룰 내용을 불릿으로 제시하세요."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return _text(response)


def write_draft(topic: str, outline: str, research_notes: str) -> str:
    """3단계: 아웃라인 + 리서치를 바탕으로 블로그 초안(마크다운) 작성"""
    system = (
        "당신은 한국어 블로그 작가입니다. "
        + REGION_GUIDELINE
        + " 결과물은 마크다운으로 작성하고, 리서치 노트에 없는 사실은 지어내지 마세요. "
        "본문에서 참고한 내용은 자연스럽게 링크로 출처를 표기하세요."
    )
    prompt = (
        f"주제: {topic}\n\n"
        f"아웃라인:\n{outline}\n\n"
        f"리서치 노트:\n{research_notes}\n\n"
        "위 아웃라인과 리서치를 바탕으로 완성된 블로그 글을 마크다운으로 작성하세요. "
        "800~1200자 분량, 도입 → 본문 → 마무리 구조로 작성하세요."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return _text(response)


def persist_blog(
    topic: str, outline: str, draft: str, final_content: str | None = None
) -> int:
    """완성된 글을 임베딩(주제 기준)과 함께 blog_posts 에 저장하고 id 를 반환한다."""
    final_content = draft if final_content is None else final_content
    return save_blog_post(
        topic=topic,
        outline=outline,
        draft=draft,
        final_content=final_content,
        embedding=embed_text(topic),
    )


def generate_blog(
    topic: str,
    on_search=None,
    threshold: float = SIMILARITY_THRESHOLD,
    reuse_existing: bool = True,
) -> dict:
    """유사 글 확인 → (없으면) 리서치 → 아웃라인 → 초안 → 저장.

    반환 dict 의 "existing" 이 True 면 DB 의 기존 글을 재사용한 것이다.
    """
    topic = topic.strip()

    # 0단계: 임베딩 유사도로 기존 글 확인
    if reuse_existing:
        found = find_similar_post(topic, threshold=threshold)
        if found:
            return {
                "existing": True,
                "similarity": found["similarity"],
                "topic": found["topic"],
                "final_content": found["final_content"],
                # 하위 호환 필드
                "research": "",
                "sources": [],
                "outline": "",
                "draft": found["final_content"],
            }

    # 1~3단계: 리서치 → 아웃라인 → 초안
    notes, sources = research(topic, on_search=on_search)
    outline = make_outline(topic, notes)
    draft = write_draft(topic, outline, notes)

    # 4단계: 임베딩과 함께 저장
    post_id = persist_blog(topic, outline, draft)

    return {
        "existing": False,
        "post_id": post_id,
        "similarity": None,
        "topic": topic,
        "research": notes,
        "sources": sources,
        "outline": outline,
        "draft": draft,
        "final_content": draft,
    }


def slugify(text: str) -> str:
    """다운로드 파일명용 슬러그 (한글 유지)"""
    slug = re.sub(r"[^\w가-힣]+", "_", text.strip())
    return slug.strip("_") or "blog"


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY가 설정되어 있지 않습니다.\n"
            ".env.example을 .env로 복사한 뒤 실제 API 키를 입력하세요."
        )
        raise SystemExit(1)

    topic = input("블로그 주제를 입력하세요 [대전 가을 축제]: ").strip() or "대전 가을 축제"
    result = generate_blog(topic)

    if result["existing"]:
        print(f"\n이미 비슷한 글이 있습니다 (유사도: {result['similarity']:.2f})")
        print(f"기존 글 주제: {result['topic']}")
        print("\n=== 기존 글 ===")
        print(result["final_content"])
    else:
        print("\n=== 1. 리서치 ===")
        print(result["research"])
        print("\n=== 출처 ===")
        for s in result["sources"]:
            print(f"- {s['title']} {s['url']}")
        print("\n=== 2. 아웃라인 ===")
        print(result["outline"])
        print("\n=== 3. 블로그 초안 ===")
        print(result["draft"])
        print(f"\n(blog_posts #{result['post_id']} 로 저장됨)")
