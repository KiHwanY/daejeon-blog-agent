"""웹 검색 tool-use 공용 모듈

`research_agent.py`(단순 Q&A)와 `blog_agent.py`(리서치 파이프라인)가 함께 쓰는
web_search 도구 정의 · DuckDuckGo 검색(+캐시) · 신뢰 소스 표시 · tool-use 루프.
"""

from functools import lru_cache
from urllib.parse import urlparse

from ddgs import DDGS

from config import MODEL
from db import find_cached_search, get_trusted_sources, save_search_cache
from embeddings import embed_text

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
) -> tuple[str, list[dict]]:
    """tool_use 루프를 돌며 (최종 텍스트, 출처 리스트) 를 반환한다.

    on_search: 검색 실행 때마다 호출되는 콜백 (query: str) -> None
    query_transform: 모델이 만든 검색어를 실제 검색 전에 가공하는 함수 (str) -> str
    """
    messages = [{"role": "user", "content": user_prompt}]
    final_text = ""
    sources: list[dict] = []
    seen: set[str] = set()

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
                query = block.input["query"]
                if query_transform:
                    query = query_transform(query)
                if on_search:
                    on_search(query)
                else:
                    print(f"[검색] {query}")

                hits = web_search(query)
                for h in hits:
                    if h["url"] and h["url"] not in seen:
                        seen.add(h["url"])
                        sources.append(h)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": format_hits(hits),
                    }
                )

        messages.append({"role": "user", "content": tool_results})

    # 신뢰 소스를 출처 목록 상단으로
    sources.sort(key=lambda h: not h.get("trusted"))
    return final_text, sources
