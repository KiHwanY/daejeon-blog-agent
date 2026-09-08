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
from functools import lru_cache

from anthropic import Anthropic
from dotenv import load_dotenv

from config import DEFAULT_REGION, MODEL, SIMILARITY_THRESHOLD
from db import find_similar_posts, get_keyword_list, save_blog_post
from embeddings import embed_text
from images import get_topic_image
from search_tools import extract_json, last_text, run_search_loop

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

# 리서치 근거가 부족할 때 초안/재작성 프롬프트에 덧붙이는 강한 제약
_LOW_GROUNDING_INSTRUCTION = (
    " [근거 부족] 이 주제는 온라인에서 확인 가능한 구체적 정보가 부족합니다. "
    "일반론·원론적 설명 위주로만 서술하고, 리서치 노트에 명시되지 않은 구체적 사실"
    "(수치·개점일·주소·가격·상호·인물명 등)은 절대 지어내지 마세요. "
    "불확실한 부분은 '~로 알려져 있습니다', '방문 전 확인이 필요합니다'처럼 완곡하게 표현하세요."
)


@lru_cache(maxsize=None)
def _keyword_list(list_name: str) -> tuple[str, ...]:
    """keyword_lists 테이블의 목록을 프로세스 1회만 조회해 캐시한다.

    DB가 없거나 비어 있으면 빈 튜플 → 해당 기능은 조용히 비활성(하드 실패 금지).
    (list_name: "local_keywords" | "region_names" | "contrast_markers")
    """
    return tuple(get_keyword_list(list_name))


def needs_local_context(text: str) -> bool:
    """지역 정보가 필요한 주제/검색어인지 여부"""
    return any(kw in text for kw in _keyword_list("local_keywords"))


def has_region_name(text: str) -> bool:
    """문자열에 이미 특정 지역명이 포함되어 있는지 여부"""
    return any(name in text for name in _keyword_list("region_names"))


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

def research(topic: str, on_search=None, on_stage=None) -> dict:
    """1단계: 주제에 대한 리서치.

    반환: {"notes", "sources", "search_retried", "research_grounded",
          "research_reason"}
    - 유효 검색 결과가 2개 이하이면 새 검색어로 1회 자동 재시도한다.
    - 루프 종료 후 근거 충분성(검증 가능한 구체 정보 확보 여부)을 1회 판정한다.
    """
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
    result = run_search_loop(
        client,
        prompt,
        system,
        on_search=on_search,
        on_stage=on_stage,
        query_transform=localize_query,
        retry_on_thin=True,
        assess_grounding=True,
    )
    return {
        "notes": result["text"],
        "sources": result["sources"],
        "search_retried": result["search_retried"],
        "research_grounded": result["research_grounded"],
        "research_reason": result["research_reason"],
    }


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
    low_grounding: bool = False,
) -> str:
    """3단계: 아웃라인 + 리서치를 바탕으로 블로그 초안(마크다운) 작성

    tone: TONE_INSTRUCTIONS 의 키 중 하나 (정보성/캐주얼/리뷰형/전문적)
    seo_keywords: 쉼표 구분 문자열 또는 리스트. 있으면 제목·본문에 3~5회 반영 지시.
    low_grounding: True 이면 '구체 사실을 지어내지 말고 일반론 위주로' 강한 제약을 추가.
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
        + (_LOW_GROUNDING_INSTRUCTION if low_grounding else "")
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


def critique_draft(
    draft: str,
    research_context: str,
    tone: str = DEFAULT_TONE,
    seo_keywords=None,
) -> dict:
    """초안을 리서치 근거·톤·SEO 기준으로 자체 평가한다.

    반환: {"pass": bool, "feedback": str}
    (모델 응답을 JSON 으로 파싱하지 못하면 통과 처리하고 그 사실을 feedback 에 남긴다.)
    """
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS[DEFAULT_TONE])
    keywords = parse_keywords(seo_keywords)
    kw_line = ", ".join(keywords) if keywords else "(지정 없음)"

    system = (
        "당신은 한국어 블로그 초안을 검수하는 엄격한 편집자입니다. "
        "아래 네 가지 기준으로만 평가하세요.\n"
        "1) 사실 정확성: 초안의 사실이 '리서치 근거'와 일치하는가, 근거에 없는 내용을 지어내지 않았는가.\n"
        "2) 구조: 도입-본문-마무리 흐름이 논리적이고 소제목 구성이 자연스러운가.\n"
        "3) 문체: 요청된 톤앤매너와 실제 문체가 일치하는가.\n"
        "4) SEO: 지정된 키워드가 자연스럽게 반영됐는가(전혀 없거나, 억지로 반복하면 실패).\n"
        "하나라도 눈에 띄게 미흡하면 pass 는 false 입니다. "
        "반드시 JSON 한 개만 출력하세요: "
        '{"pass": true 또는 false, "feedback": "구체적 지적 또는 통과 사유 1~3문장"}'
    )
    prompt = (
        f"[요청한 톤앤매너] {tone}: {tone_instruction}\n"
        f"[지정 SEO 키워드] {kw_line}\n\n"
        f"[리서치 근거]\n{research_context or '(근거 없음)'}\n\n"
        f"[검수할 초안]\n{draft}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    data = extract_json(last_text(response))
    if not data or "pass" not in data:
        return {
            "pass": True,
            "feedback": "(자체 검토 응답을 해석하지 못해 통과 처리했습니다.)",
        }
    return {
        "pass": bool(data.get("pass")),
        "feedback": str(data.get("feedback") or "").strip(),
    }


def rewrite_draft(
    topic: str,
    outline: str,
    research_notes: str,
    previous_draft: str,
    feedback: str,
    tone: str = DEFAULT_TONE,
    seo_keywords=None,
    low_grounding: bool = False,
) -> str:
    """자체 검토 피드백을 반영해 초안을 1회 재작성한다."""
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS[DEFAULT_TONE])
    keywords = parse_keywords(seo_keywords)
    seo_instruction = ""
    if keywords:
        seo_instruction = (
            f" 다음 키워드를 글 전체에 자연스럽게 각각 3~5회 반영하되, 한 단락이나 "
            f"한 문장에 몰아넣지 말고 인트로·본문·결론에 고르게 나눠 배치하세요: "
            f"{', '.join(keywords)}."
        )

    anti_repetition = (
        " [재작성 규칙] 피드백에서 지적된 문제를 고치되, 이전 초안과 동일한 수사법·문장 "
        "구조를 반복하지 마세요. 특히 SEO 키워드를 본문 주제와 대비·구별짓는 방식"
        "('~와 달리', '~와는 무관하게', '~와 다른 결' 등)으로 여러 번 반복하지 마세요. "
        "키워드는 인트로·본문·결론에서 각각 서로 다른 방식으로 녹여내고, 한 구간에서는 "
        "최대 1~2회만 자연스러운 맥락으로 언급하세요(키워드별 총 등장 횟수 목표는 유지, "
        "같은 문장 패턴 재사용은 금지)."
    )

    system = (
        "당신은 한국어 블로그 작가입니다. "
        + REGION_GUIDELINE
        + " 검수자의 피드백을 반영해 초안을 고쳐 씁니다. "
        "리서치 노트에 없는 사실은 지어내지 말고, 마크다운으로 작성하세요."
        + f" [톤앤매너] {tone_instruction}"
        + seo_instruction
        + anti_repetition
        + (_LOW_GROUNDING_INSTRUCTION if low_grounding else "")
    )
    prompt = (
        f"주제: {topic}\n\n"
        f"아웃라인:\n{outline}\n\n"
        f"리서치 노트:\n{research_notes}\n\n"
        f"이전 초안:\n{previous_draft}\n\n"
        f"검수 피드백(반드시 반영):\n{feedback}\n\n"
        "위 피드백을 모두 반영하되 이전 초안의 문장 패턴을 그대로 되풀이하지 말고, "
        "800~1200자 분량의 완성된 블로그 글을 다시 작성하세요."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    # 재작성 응답이 비면(제약 충돌 등) 빈 글 대신 이전 초안을 유지한다.
    return last_text(response) or previous_draft


def _split_sentences(text: str) -> list[str]:
    """마침표·물음표·느낌표·줄바꿈 기준으로 대략적인 문장 리스트를 만든다."""
    return [s.strip() for s in re.split(r"[.!?\n]+", text or "") if s.strip()]


def check_contrast_repetition(text: str, seo_keywords, threshold: int = 3) -> dict:
    """규칙 기반(무 LLM) 반복 패턴 점검.

    '대비 표현'(keyword_lists 의 contrast_markers)과 SEO 키워드가 같은 문장에
    함께 등장하는 빈도를 센다. threshold 회 이상이면 repetitive=True.

    반환: {"count": int, "repetitive": bool, "examples": list[str]}
    """
    keywords = parse_keywords(seo_keywords)
    if not keywords:
        return {"count": 0, "repetitive": False, "examples": []}

    markers = _keyword_list("contrast_markers")
    hits = []
    for sentence in _split_sentences(text):
        has_contrast = any(marker in sentence for marker in markers)
        has_keyword = any(kw in sentence for kw in keywords)
        if has_contrast and has_keyword:
            hits.append(sentence)

    return {
        "count": len(hits),
        "repetitive": len(hits) >= threshold,
        "examples": hits[:3],
    }


def persist_blog(
    topic: str,
    outline: str,
    draft: str,
    final_content: str | None = None,
    tone: str | None = None,
    seo_keywords=None,
    image_url: str | None = None,
    critique_passed: bool | None = None,
    critique_feedback: str | None = None,
    was_rewritten: bool = False,
    search_retried: bool = False,
    research_grounded: bool | None = None,
    research_reason: str | None = None,
) -> int:
    """완성된 글을 임베딩·톤·SEO·이미지·자체검토 메타와 함께 blog_posts 에 저장하고 id 반환."""
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
        critique_passed=critique_passed,
        critique_feedback=critique_feedback,
        was_rewritten=was_rewritten,
        search_retried=search_retried,
        research_grounded=research_grounded,
        research_reason=research_reason,
    )


def _emit(on_stage, stage: str, state: str, **data) -> None:
    """진행 상황 콜백 헬퍼. on_stage 가 있으면 {stage, state, ...} 이벤트를 넘긴다.

    stage: "similar" | "research" | "grounding" | "outline" | "draft"
           | "critique" | "rewrite" | "image" | "save"
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
    abort_on_weak_research: bool = False,
) -> dict:
    """유사 글 확인 → (없으면) 리서치 → 근거 확인 → 아웃라인 → 초안 → 자체 검토 → 저장.

    on_search: 검색 실행 때마다 호출 (query: str) -> None
    on_stage:  단계 전환 때마다 호출 (event: dict) -> None
    tone: 초안 톤앤매너 (정보성/캐주얼/리뷰형/전문적)
    seo_keywords: 쉼표 구분 문자열 또는 리스트
    abort_on_weak_research:
        - False(기본, 옵션 B): 근거가 부족해도 "일반론 위주·사실 지어내기 금지" 제약을
          걸고 초안을 생성하되, 결과에 research_grounded=False 경고를 담는다.
        - True(옵션 A): 근거가 부족하면 초안을 만들지 않고 "aborted": True 로 즉시 반환.

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
            "aborted": False,
            "similar_posts": posts,
            "post_id": top.get("id"),
            "similarity": top["similarity"],
            "topic": top["topic"],
            "tone": top.get("tone"),
            "seo_keywords": keywords,
            "seo_report": count_keyword_occurrences(top["final_content"], keywords),
            "final_content": top["final_content"],
            "image_url": top.get("image_url"),
            "search_retried": top.get("search_retried"),
            "critique_passed": top.get("critique_passed"),
            "critique_feedback": top.get("critique_feedback"),
            "was_rewritten": top.get("was_rewritten"),
            "research_grounded": top.get("research_grounded"),
            "research_reason": top.get("research_reason"),
            # 하위 호환 필드
            "research": "",
            "sources": [],
            "outline": "",
            "draft": top["final_content"],
        }

    # 1단계: 리서치 (결과 부족 시 재검색 + 근거 충분성 판정)
    _emit(on_stage, "research", "start")
    research_result = research(topic, on_search=on_search, on_stage=on_stage)
    notes = research_result["notes"]
    sources = research_result["sources"]
    search_retried = research_result["search_retried"]
    research_grounded = research_result["research_grounded"]
    research_reason = research_result["research_reason"]
    _emit(
        on_stage, "research", "done",
        notes=notes, sources=sources, search_retried=search_retried,
    )

    low_grounding = not research_grounded

    # 근거가 부족한데 옵션 A(중단)면 초안을 만들지 않고 즉시 반환
    if low_grounding and abort_on_weak_research:
        return {
            "existing": False,
            "aborted": True,
            "similar_posts": [],
            "post_id": None,
            "similarity": None,
            "topic": topic,
            "tone": tone,
            "seo_keywords": keywords,
            "seo_report": {},
            "research": notes,
            "sources": sources,
            "outline": "",
            "draft": "",
            "final_content": "",
            "image_url": None,
            "search_retried": search_retried,
            "critique_passed": None,
            "critique_feedback": None,
            "was_rewritten": False,
            "research_grounded": False,
            "research_reason": research_reason,
        }

    # 2단계: 아웃라인
    _emit(on_stage, "outline", "start")
    outline = make_outline(topic, notes)
    _emit(on_stage, "outline", "done", outline=outline)

    # 3단계: 초안(톤·SEO 반영, 근거 부족 시 강한 제약)
    _emit(on_stage, "draft", "start", tone=tone, low_grounding=low_grounding)
    draft = write_draft(
        topic, outline, notes, tone=tone, seo_keywords=keywords,
        low_grounding=low_grounding,
    )
    _emit(on_stage, "draft", "done", draft=draft)

    # 4단계: 자체 품질 검토 → 미흡하면 피드백 반영해 1회만 재작성
    _emit(on_stage, "critique", "start")
    critique = critique_draft(draft, notes, tone=tone, seo_keywords=keywords)
    critique_passed = critique["pass"]
    critique_feedback = critique["feedback"]
    _emit(
        on_stage, "critique", "done",
        passed=critique_passed, feedback=critique_feedback,
    )

    was_rewritten = False
    if not critique_passed:
        _emit(on_stage, "rewrite", "start", feedback=critique_feedback)
        draft = rewrite_draft(
            topic, outline, notes, draft, critique_feedback,
            tone=tone, seo_keywords=keywords, low_grounding=low_grounding,
        )
        was_rewritten = True

        # 가벼운 안전장치: 재작성본에 '대비 표현 + SEO 키워드' 상투구가 여전히
        # 반복되는지 규칙 기반으로만 점검한다(2차 LLM 재작성 없이 기록만).
        repetition = check_contrast_repetition(draft, keywords)
        if repetition["repetitive"]:
            critique_feedback = (
                (critique_feedback or "").rstrip()
                + f" ⚠️[자동 점검] 재작성 후에도 '대비 표현 + SEO 키워드' 상투 구문이 "
                f"{repetition['count']}회 반복 감지되어 수사 패턴이 단조로울 수 있습니다."
            )
        _emit(
            on_stage, "rewrite", "done",
            repetition_count=repetition["count"],
            repetition_warning=repetition["repetitive"],
        )

    seo_report = count_keyword_occurrences(draft, keywords)

    # 5단계: 주제에 어울리는 대표 이미지 검색 (실패해도 진행)
    _emit(on_stage, "image", "start")
    image_url = get_topic_image(topic)
    _emit(on_stage, "image", "done", image_url=image_url)

    # 6단계: 임베딩·톤·SEO·이미지·자체검토·근거 메타와 함께 저장
    _emit(on_stage, "save", "start")
    post_id = persist_blog(
        topic, outline, draft, tone=tone, seo_keywords=keywords, image_url=image_url,
        critique_passed=critique_passed, critique_feedback=critique_feedback,
        was_rewritten=was_rewritten, search_retried=search_retried,
        research_grounded=research_grounded, research_reason=research_reason,
    )
    _emit(on_stage, "save", "done", post_id=post_id)

    return {
        "existing": False,
        "aborted": False,
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
        "search_retried": search_retried,
        "critique_passed": critique_passed,
        "critique_feedback": critique_feedback,
        "was_rewritten": was_rewritten,
        "research_grounded": research_grounded,
        "research_reason": research_reason,
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

    if result.get("aborted"):
        print("\n=== 생성 중단 ===")
        print("리서치 근거가 부족해 초안 생성을 중단했습니다.")
        print(f"사유: {result.get('research_reason')}")
        raise SystemExit(0)

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

    if not result["existing"]:
        print("\n=== 에이전트 자체 판단 ===")
        grounded = result.get("research_grounded")
        print(f"- 리서치 근거: {'충분' if grounded else '부족 ⚠️'}")
        if result.get("research_reason"):
            print(f"  · {result['research_reason']}")
        print(f"- 검색 재시도: {'예' if result.get('search_retried') else '아니오'}")
        passed = result.get("critique_passed")
        print(f"- 자체 검토: {'통과' if passed else '미통과'}")
        print(f"- 재작성: {'예' if result.get('was_rewritten') else '아니오'}")
        if result.get("critique_feedback"):
            print(f"- 피드백: {result['critique_feedback']}")
