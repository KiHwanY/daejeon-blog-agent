"""웹 검색 tool-use 공용 모듈

`research_agent.py`(단순 Q&A)와 `blog_agent.py`(리서치 파이프라인)가 함께 쓰는
web_search 도구 정의 · DuckDuckGo 검색(+캐시) · 신뢰 소스 표시 · tool-use 루프.
"""

import json
import re
from functools import lru_cache
from urllib.parse import urlparse

from ddgs import DDGS

from config import MODEL
from db import find_cached_search, get_trusted_sources, save_search_cache
from embeddings import embed_text


def extract_json(text: str) -> dict | None:
    """LLM 응답 텍스트에서 첫 JSON 오브젝트를 추출해 dict 로 반환한다. 실패 시 None.

    ```json ... ``` 펜스나 앞뒤 설명이 섞여 있어도 최대한 뽑아낸다.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return None
    try:
        value = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


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


@lru_cache(maxsize=1)
def _trusted_sources() -> tuple[tuple[str, str], ...]:
    """trusted_sources 를 (도메인, 표시명) 튜플 목록으로 캐시한다(DB 조회 1회).

    DB가 없거나 비어 있으면 빈 튜플 → 신뢰 소스 가점 없이 그대로 진행.
    """
    return tuple(
        (s["domain"].lower(), s.get("name") or s["domain"])
        for s in get_trusted_sources()
        if s.get("domain")
    )


def match_trusted(url: str) -> str | None:
    """url 이 신뢰 도메인(또는 그 하위 도메인)이면 표시명을, 아니면 None 을 반환한다."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    for domain, name in _trusted_sources():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def annotate_trust(hits: list[dict]) -> list[dict]:
    """각 검색 결과에 trusted(bool)·source_name 을 덧붙인다."""
    annotated = []
    for h in hits:
        name = match_trusted(h.get("url") or "")
        annotated.append({**h, "trusted": bool(name), "source_name": name})
    return annotated


def web_search(query: str, max_results: int = 5, use_cache: bool = True) -> list[dict]:
    """DuckDuckGo 웹 검색 실행 → [{title, url, snippet, trusted, source_name}, ...] 반환

    use_cache=True 면 search_cache 를 먼저 조회하고, 신규 검색 결과는 캐시에 저장한다.
    신뢰 소스 표시는 캐시된 결과에도 조회 시점 기준으로 다시 계산한다.
    """
    q_embedding = None
    if use_cache:
        try:
            q_embedding = embed_text(query)
            cached = find_cached_search(query)
        except Exception:  # noqa: BLE001 - 캐시 실패는 무시하고 실검색으로
            cached = None
        if cached is not None:
            return annotate_trust(cached)

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:  # noqa: BLE001 - 검색 실패는 도구 결과로 전달
        return [
            {
                "title": "",
                "url": "",
                "snippet": f"검색 중 오류가 발생했습니다: {e}",
                "trusted": False,
                "source_name": None,
            }
        ]

    raw_hits = [
        {
            "title": r.get("title") or "",
            "url": r.get("href") or "",
            "snippet": r.get("body") or "",
        }
        for r in results
    ]

    if use_cache and q_embedding is not None:
        try:
            save_search_cache(query, raw_hits, embedding=q_embedding)
        except Exception:  # noqa: BLE001 - 캐시 저장 실패는 조용히 무시
            pass

    return annotate_trust(raw_hits)


def format_hits(hits: list[dict]) -> str:
    """검색 결과를 모델에게 전달할 텍스트로 변환한다(신뢰 소스를 상단에)."""
    if not hits:
        return "검색 결과가 없습니다."
    ordered = sorted(hits, key=lambda h: not h.get("trusted"))
    lines = []
    for h in ordered:
        tag = f" [신뢰 소스: {h['source_name']}]" if h.get("trusted") else ""
        lines.append(
            f"- 제목: {h['title']}{tag}\n  URL: {h['url']}\n  요약: {h['snippet']}"
        )
    return "\n".join(lines)


def last_text(response) -> str:
    """응답에서 마지막 텍스트 블록을 뽑아 반환"""
    out = ""
    for block in response.content:
        if block.type == "text" and block.text.strip():
            out = block.text
    return out


# 결과가 부족할 때 재검색 키워드를 물어보는 지시문
_RETRY_SYSTEM = (
    "직전 웹 검색 결과가 부족했습니다. 사용자 요청과 이미 시도한 검색어를 보고 "
    "(1) 결과가 부족했던 이유를 한 문장으로, "
    "(2) 이전과 다른 각도의 더 나은 검색어(한국어, 3~6단어)를 제시하세요. "
    'JSON 한 줄로만 답하세요: {"reason": "...", "keywords": "..."}'
)


def _suggest_retry_keywords(client, model, user_prompt, tried_queries) -> str | None:
    """검색 결과가 부족했을 때 Claude 에게 새 검색어를 물어본다. 실패하면 None."""
    ask = (
        f"사용자 요청:\n{user_prompt}\n\n"
        f"이미 시도한 검색어: {tried_queries or '(없음)'}\n"
        "이와 겹치지 않는 새 검색어가 필요합니다."
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=_RETRY_SYSTEM,
            messages=[{"role": "user", "content": ask}],
        )
    except Exception:  # noqa: BLE001 - 재시도 제안 실패 시 그냥 재시도 안 함
        return None

    data = extract_json(last_text(response)) or {}
    keywords = (data.get("keywords") or "").strip()
    return keywords or None


def run_search_loop(
    client,
    user_prompt: str,
    system_prompt: str,
    *,
    max_turns: int = 5,
    on_search=None,
    query_transform=None,
    model: str = MODEL,
    max_tokens: int = 3000,
    retry_on_thin: bool = False,
    min_results: int = 2,
) -> dict:
    """tool_use 루프를 돌며 {"text", "sources", "search_retried"} 를 반환한다.

    on_search: 검색 실행 때마다 호출되는 콜백 (query: str) -> None
    query_transform: 모델이 만든 검색어를 실제 검색 전에 가공하는 함수 (str) -> str
    retry_on_thin: True 이고 유효 검색 결과가 min_results 이하이면, Claude 에게
        새 검색어를 물어 1회만 추가 검색한다(무한 재시도 방지).
    """
    messages = [{"role": "user", "content": user_prompt}]
    final_text = ""
    sources: list[dict] = []
    seen: set[str] = set()
    tried_queries: list[str] = []

    def _do_search(query: str) -> list[dict]:
        if query_transform:
            query = query_transform(query)
        tried_queries.append(query)
        if on_search:
            on_search(query)
        else:
            print(f"[검색] {query}")
        hits = web_search(query)
        for h in hits:
            if h["url"] and h["url"] not in seen:
                seen.add(h["url"])
                sources.append(h)
        return hits

    for _ in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        block_text = last_text(response)
        if block_text:
            final_text = block_text

        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "web_search":
                hits = _do_search(block.input["query"])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": format_hits(hits),
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    # 결과가 부족하면 새 검색어로 딱 1회 재시도
    search_retried = False
    if retry_on_thin and len(sources) <= min_results:
        new_keywords = _suggest_retry_keywords(
            client, model, user_prompt, tried_queries
        )
        if new_keywords:
            search_retried = True
            _do_search(new_keywords)

    # 루프가 최종 정리 없이 도구 호출만 하다 끝났으면(max_turns 도달) 정리 1회 요청
    if (
        not final_text
        and sources
        and messages
        and messages[-1]["role"] == "user"
        and isinstance(messages[-1]["content"], list)
    ):
        messages[-1]["content"].append(
            {
                "type": "text",
                "text": "추가 검색은 하지 말고, 지금까지 찾은 내용만으로 최종 정리를 작성하세요.",
            }
        )
        try:
            wrap = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            )
            final_text = last_text(wrap) or final_text
        except Exception:  # noqa: BLE001 - 정리 실패해도 지금까지 결과로 진행
            pass

    # 신뢰 소스를 출처 목록 상단으로
    sources.sort(key=lambda h: not h.get("trusted"))
    return {"text": final_text, "sources": sources, "search_retried": search_retried}
