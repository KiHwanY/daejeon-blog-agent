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
from dotenv import load_dotenv

from config import DEFAULT_REGION, MODEL, SIMILARITY_THRESHOLD
from db import find_similar_posts, save_blog_post
from embeddings import embed_text
from images import get_topic_image
from search_tools import last_text, run_search_loop

# 프로젝트 루트(혹은 상위 경로)의 .env 파일에서 환경변수를 읽어온다.
load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# 톤앤매너별 초안 작성 지시문
TONE_INSTRUCTIONS = {
    "정보성": (
        "객관적 사실 중심으로, 정중한 설명체(합니다체)로 작성하세요. "
        "개인적 감상보다 정확한 정보 전달에 집중합니다."
    ),
    "캐주얼": (
        "친근한 반말투로 편하게 대화하듯 작성하고, 이모티콘을 문단마다 1개 이내로 "
        "가볍게 섞으세요. 너무 딱딱하지 않게, 옆에서 얘기해 주는 느낌으로."
    ),
    "리뷰형": (
        "1인칭 경험담 스타일로, 직접 방문하거나 사용해 본 것처럼 생생하게 서술하세요. "
        "글 마지막에는 장점과 단점을 각각 정리해 제시합니다."
    ),
    "전문적": (
        "격식체로 작성하고, 가능한 한 구체적인 데이터·수치·근거를 제시하며 "
        "신뢰감 있고 분석적인 어조로 서술하세요."
    ),
}
DEFAULT_TONE = "정보성"

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


def parse_keywords(seo_keywords) -> list[str]:
    """쉼표로 구분된 SEO 키워드(문자열 또는 리스트)를 정규화된 리스트로 변환"""
    if not seo_keywords:
        return []
    parts = seo_keywords.split(",") if isinstance(seo_keywords, str) else list(seo_keywords)
    return [p.strip() for p in parts if p and p.strip()]


def keywords_to_str(seo_keywords) -> str | None:
    """SEO 키워드를 DB 저장용 문자열('kw1, kw2')로. 없으면 None."""
    kws = parse_keywords(seo_keywords)
    return ", ".join(kws) if kws else None


def count_keyword_occurrences(text, seo_keywords) -> dict:
    """본문에서 각 SEO 키워드가 몇 번 등장했는지 세어 {키워드: 횟수} 로 반환"""
    text = text or ""
    return {kw: text.count(kw) for kw in parse_keywords(seo_keywords)}


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
        "검색 결과에 '[신뢰 소스: ...]'로 표시된 항목(대전시청·대전관광공사·지역 언론 등)은 "
        "다른 출처보다 우선해서 인용하고, 사실이 엇갈리면 신뢰 소스를 기준으로 삼으세요. "
        "조사한 내용은 추측 없이 사실 위주로 불릿으로 정리하고, "
        "각 항목 끝에 참고한 출처 URL을 함께 적으세요."
    )
    prompt = f"다음 주제로 블로그 글을 쓰기 위한 리서치를 해주세요: {topic}"
    return run_search_loop(
        client,
        prompt,
        system,
        on_search=on_search,
        query_transform=localize_query,
    )


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
    return last_text(response)


def write_draft(
    topic: str,
    outline: str,
    research_notes: str,
    tone: str = DEFAULT_TONE,
    seo_keywords=None,
) -> str:
    """3단계: 아웃라인 + 리서치를 바탕으로 블로그 초안(마크다운) 작성

    tone: TONE_INSTRUCTIONS 의 키 중 하나 (정보성/캐주얼/리뷰형/전문적)
    seo_keywords: 쉼표 구분 문자열 또는 리스트. 있으면 제목·본문에 3~5회 반영 지시.
    """
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS[DEFAULT_TONE])

    keywords = parse_keywords(seo_keywords)
    seo_instruction = ""
    if keywords:
        seo_instruction = (
            f" [SEO] 다음 키워드를 제목과 본문에 자연스럽게 각각 3~5회 정도 등장시키세요: "
            f"{', '.join(keywords)}. 억지로 반복하지 말고 문맥에 맞게 녹여 쓰세요."
        )

    system = (
        "당신은 한국어 블로그 작가입니다. "
        + REGION_GUIDELINE
        + " 결과물은 마크다운으로 작성하고, 리서치 노트에 없는 사실은 지어내지 마세요. "
        "본문에서 참고한 내용은 자연스럽게 링크로 출처를 표기하세요."
        + f" [톤앤매너] {tone_instruction}"
        + seo_instruction
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
    return last_text(response)


def persist_blog(
    topic: str,
    outline: str,
    draft: str,
    final_content: str | None = None,
    tone: str | None = None,
    seo_keywords=None,
    image_url: str | None = None,
) -> int:
    """완성된 글을 임베딩(주제 기준)·톤·SEO 키워드·이미지와 함께 blog_posts 에 저장하고 id 반환."""
    final_content = draft if final_content is None else final_content
    return save_blog_post(
        topic=topic,
        outline=outline,
        draft=draft,
        final_content=final_content,
        embedding=embed_text(topic),
        tone=tone,
        seo_keywords=keywords_to_str(seo_keywords),
        image_url=image_url,
    )


def _emit(on_stage, stage: str, state: str, **data) -> None:
    """진행 상황 콜백 헬퍼. on_stage 가 있으면 {stage, state, ...} 이벤트를 넘긴다.

    stage: "similar" | "research" | "outline" | "draft" | "image" | "save"
    state: "start" | "done"
    """
    if on_stage:
        on_stage({"stage": stage, "state": state, **data})


def generate_blog(
    topic: str,
    on_search=None,
    on_stage=None,
    threshold: float = SIMILARITY_THRESHOLD,
    reuse_existing: bool = True,
    similar_limit: int = 6,
    tone: str = DEFAULT_TONE,
    seo_keywords=None,
) -> dict:
    """유사 글 확인 → (없으면) 리서치 → 아웃라인 → 초안 → 저장.

    on_search: 검색 실행 때마다 호출 (query: str) -> None
    on_stage:  단계 전환 때마다 호출 (event: dict) -> None
    tone: 초안 톤앤매너 (정보성/캐주얼/리뷰형/전문적)
    seo_keywords: 쉼표 구분 문자열 또는 리스트

    반환 dict 의 "existing" 이 True 면 DB 의 기존 글을 재사용한 것이며,
    이때 "similar_posts" 에 유사 글 목록이 함께 담긴다.
    "seo_report" 는 {키워드: 본문 등장 횟수}.
    """
    topic = topic.strip()
    keywords = parse_keywords(seo_keywords)

    # 0단계: 임베딩 유사도로 기존 글 확인
    _emit(on_stage, "similar", "start")
    posts = (
        find_similar_posts(topic, threshold=threshold, limit=similar_limit)
        if reuse_existing
        else []
    )
    _emit(on_stage, "similar", "done", posts=posts)

    if posts:
        top = posts[0]
        return {
            "existing": True,
            "similar_posts": posts,
            "post_id": top.get("id"),
            "similarity": top["similarity"],
            "topic": top["topic"],
            "tone": top.get("tone"),
            "seo_keywords": keywords,
            "seo_report": count_keyword_occurrences(top["final_content"], keywords),
            "final_content": top["final_content"],
            "image_url": top.get("image_url"),
            # 하위 호환 필드
            "research": "",
            "sources": [],
            "outline": "",
            "draft": top["final_content"],
        }

    # 1단계: 리서치
    _emit(on_stage, "research", "start")
    notes, sources = research(topic, on_search=on_search)
    _emit(on_stage, "research", "done", notes=notes, sources=sources)

    # 2단계: 아웃라인
    _emit(on_stage, "outline", "start")
    outline = make_outline(topic, notes)
    _emit(on_stage, "outline", "done", outline=outline)

    # 3단계: 초안(톤·SEO 반영)
    _emit(on_stage, "draft", "start", tone=tone)
    draft = write_draft(topic, outline, notes, tone=tone, seo_keywords=keywords)
    _emit(on_stage, "draft", "done", draft=draft)

    seo_report = count_keyword_occurrences(draft, keywords)

    # 4단계: 주제에 어울리는 대표 이미지 검색 (실패해도 진행)
    _emit(on_stage, "image", "start")
    image_url = get_topic_image(topic)
    _emit(on_stage, "image", "done", image_url=image_url)

    # 5단계: 임베딩·톤·SEO 키워드·이미지와 함께 저장
    _emit(on_stage, "save", "start")
    post_id = persist_blog(
        topic, outline, draft, tone=tone, seo_keywords=keywords, image_url=image_url
    )
    _emit(on_stage, "save", "done", post_id=post_id)

    return {
        "existing": False,
        "similar_posts": [],
        "post_id": post_id,
        "similarity": None,
        "topic": topic,
        "tone": tone,
        "seo_keywords": keywords,
        "seo_report": seo_report,
        "research": notes,
        "sources": sources,
        "outline": outline,
        "draft": draft,
        "final_content": draft,
        "image_url": image_url,
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
    tone = (
        input("톤앤매너 [정보성/캐주얼/리뷰형/전문적] (기본 정보성): ").strip()
        or DEFAULT_TONE
    )
    seo = input("SEO 키워드 (쉼표 구분, 없으면 엔터): ").strip()

    result = generate_blog(topic, tone=tone, seo_keywords=seo)

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
        print(f"\n=== 3. 블로그 초안 (톤: {result['tone']}) ===")
        print(result["draft"])
        print(f"\n(blog_posts #{result['post_id']} 로 저장됨)")

    if result.get("seo_report"):
        print("\n=== SEO 키워드 체크 ===")
        for kw, cnt in result["seo_report"].items():
            print(f"- {kw}: {cnt}회")
