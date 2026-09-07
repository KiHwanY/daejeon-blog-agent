"""Pexels 이미지 검색 — 블로그 주제에 어울리는 대표 이미지 URL 하나를 찾는다.

- 한국어 주제를 Claude 로 2~4단어 영어 검색어로 변환한 뒤 Pexels 로 검색한다.
- 키가 없거나 API 오류·결과 없음이면 예외를 던지지 않고 None 을 반환한다.

사용:
  from images import get_topic_image
  url = get_topic_image("대전 성심당 빵집 추천")   # -> str | None
"""

import os

import httpx
from anthropic import Anthropic

from config import MODEL, PEXELS_API_KEY, PEXELS_SEARCH_URL

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# 주제 → 영어 이미지 검색어 변환 지시문
_KEYWORD_SYSTEM = (
    "You convert a blog post topic (often Korean) into a short English image "
    "search query. Reply with ONLY 2 to 4 lowercase English words naming the "
    "main visual subject — no punctuation, no quotes, no explanation. "
    "Example: topic '대전 성심당 빵집 추천' -> 'korean bakery bread'."
)

_HTTP_TIMEOUT = 10.0


def _english_keywords(topic: str) -> str | None:
    """주제를 영어 이미지 검색어(2~4단어)로 변환한다. 실패하면 None."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=30,
            system=_KEYWORD_SYSTEM,
            messages=[{"role": "user", "content": topic}],
        )
    except Exception:  # noqa: BLE001 - 변환 실패 시 이미지 없이 진행
        return None

    text = " ".join(
        block.text
        for block in resp.content
        if getattr(block, "type", None) == "text"
    ).strip().strip("\"'")

    words = text.split()[:4]
    return " ".join(words).lower() or None


def _pexels_search(query: str) -> str | None:
    """Pexels 에서 query 로 검색해 가로형 사진 URL 하나를 반환한다. 실패하면 None."""
    try:
        resp = httpx.get(
            PEXELS_SEARCH_URL,
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 - 네트워크/HTTP/JSON 오류는 모두 이미지 없음 처리
        return None

    photos = data.get("photos") or []
    if not photos:
        return None
    src = photos[0].get("src") or {}
    return src.get("landscape") or src.get("large") or src.get("original") or None


def get_topic_image(topic: str) -> str | None:
    """주제에 어울리는 Pexels 이미지 URL 하나를 반환한다. 없으면 None.

    - PEXELS_API_KEY 가 없으면 곧바로 None.
    - 한국어 주제는 Claude 로 영어 검색어로 바꾼 뒤 검색한다(변환 실패 시 None).
    """
    topic = (topic or "").strip()
    if not topic or not PEXELS_API_KEY:
        return None

    query = _english_keywords(topic)
    if not query:
        return None

    return _pexels_search(query)
