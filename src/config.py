"""공용 설정값

여러 모듈에서 쓰이던 중복 상수를 한곳에 모아 둔다.
(`src/` 가 import 경로에 있으므로 `from config import ...` 로 가져온다.)
"""

# Claude 모델 ID
MODEL = "claude-sonnet-5"

# 유사 글로 간주하는 코사인 유사도 하한
SIMILARITY_THRESHOLD = 0.85

# 검색 캐시를 재사용하는 코사인 유사도 하한(검색어가 거의 동일할 때만 재사용)
SEARCH_CACHE_SIMILARITY = 0.97

# 검색 캐시 유효 시간(시간). 이보다 오래된 캐시는 무시한다.
SEARCH_CACHE_TTL_HOURS = 24 * 7

# 사용자의 기본 지역 맥락
DEFAULT_REGION = "대전"
