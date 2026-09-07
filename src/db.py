"""
PostgreSQL(pgvector) 연결 및 스키마 초기화

.env 의 DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD 를 사용한다.

사용:
  python src/db.py        # vector 확장 + 테이블 생성 + 기본 도메인 시딩 + 검증 출력
"""

import json
import os

import psycopg2
from dotenv import load_dotenv

from config import (
    SEARCH_CACHE_SIMILARITY,
    SEARCH_CACHE_TTL_HOURS,
    SIMILARITY_THRESHOLD,
)
from embeddings import embed_text

# 프로젝트 루트(혹은 상위 경로)의 .env 를 읽어온다.
load_dotenv()


def get_connection():
    """.env 의 접속 정보로 PostgreSQL 커넥션을 만들어 반환한다."""
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


# 순서대로 실행되는 스키마 DDL
SCHEMA_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector;",
    """
    CREATE TABLE IF NOT EXISTS trusted_sources (
        id         SERIAL PRIMARY KEY,
        domain     TEXT UNIQUE,
        name       TEXT,
        category   TEXT,
        created_at TIMESTAMP DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS blog_posts (
        id            SERIAL PRIMARY KEY,
        topic         TEXT,
        outline       TEXT,
        draft         TEXT,
        final_content TEXT,
        tone          TEXT,
        seo_keywords  TEXT,
        embedding     vector(768),
        created_at    TIMESTAMP DEFAULT now()
    );
    """,
    # 기존 blog_posts 테이블을 위한 컬럼 추가 (멱등)
    "ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS tone TEXT;",
    "ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS seo_keywords TEXT;",
    "ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS image_url TEXT;",
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        id           SERIAL PRIMARY KEY,
        query        TEXT,
        results_json TEXT,
        embedding    vector(768),
        created_at   TIMESTAMP DEFAULT now()
    );
    """,
    # 코사인 거리(<=>) 최근접 검색용 HNSW 인덱스 (pgvector >= 0.5).
    # 데이터가 많아져도 유사도 조회가 순차 스캔으로 느려지지 않도록 한다.
    """
    CREATE INDEX IF NOT EXISTS blog_posts_embedding_hnsw
        ON blog_posts USING hnsw (embedding vector_cosine_ops);
    """,
    """
    CREATE INDEX IF NOT EXISTS search_cache_embedding_hnsw
        ON search_cache USING hnsw (embedding vector_cosine_ops);
    """,
    # 검색 캐시 TTL 필터(created_at > now() - interval)용 인덱스
    """
    CREATE INDEX IF NOT EXISTS search_cache_created_at
        ON search_cache (created_at DESC);
    """,
]

# trusted_sources 기본 시드 (대전 관련 도메인)
TRUSTED_SOURCE_SEEDS = [
    ("daejeon.go.kr", "대전광역시청", "공식기관"),
    ("djto.kr", "대전관광공사", "공식기관"),
    ("daejonilbo.com", "대전일보", "지역언론"),
    ("joongdo.co.kr", "중도일보", "지역언론"),
]


def init_db() -> None:
    """확장/테이블 생성 후 기본 도메인을 시딩한다. (멱등)"""
    conn = get_connection()
    try:
        with conn:  # 블록이 예외 없이 끝나면 커밋
            with conn.cursor() as cur:
                for stmt in SCHEMA_STATEMENTS:
                    cur.execute(stmt)

                cur.executemany(
                    """
                    INSERT INTO trusted_sources (domain, name, category)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (domain) DO NOTHING;
                    """,
                    TRUSTED_SOURCE_SEEDS,
                )
    finally:
        conn.close()


def _to_vector_literal(vec) -> str:
    """float 리스트를 pgvector 리터럴 문자열 '[v1,v2,...]' 로 변환한다."""
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def find_similar_posts(
    topic: str, threshold: float = SIMILARITY_THRESHOLD, limit: int = 6
) -> list[dict]:
    """topic 과 유사한 글들을 유사도 내림차순으로 최대 limit 개 반환한다.

    각 항목: {id, topic, final_content, tone, seo_keywords, image_url,
             created_at, similarity}
    유사도가 threshold 미만인 글은 제외한다.
    """
    vec_literal = _to_vector_literal(embed_text(topic))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, topic, final_content, tone, seo_keywords, image_url,
                       created_at,
                       1 - (embedding <=> %(v)s::vector) AS similarity
                FROM blog_posts
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %(v)s::vector
                LIMIT %(lim)s;
                """,
                {"v": vec_literal, "lim": limit},
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    posts = []
    for r in rows:
        similarity = float(r[7])
        if similarity < threshold:
            continue
        posts.append(
            {
                "id": r[0],
                "topic": r[1],
                "final_content": r[2],
                "tone": r[3],
                "seo_keywords": r[4],
                "image_url": r[5],
                "created_at": r[6],
                "similarity": similarity,
            }
        )
    return posts


def save_blog_post(
    topic,
    outline,
    draft,
    final_content,
    embedding=None,
    tone=None,
    seo_keywords=None,
    image_url=None,
) -> int:
    """완성된 글을 blog_posts 에 저장하고 새 id 를 반환한다."""
    vec_literal = _to_vector_literal(embedding) if embedding is not None else None

    conn = get_connection()
    try:
        with conn:  # 예외 없이 끝나면 커밋
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO blog_posts
                        (topic, outline, draft, final_content,
                         tone, seo_keywords, image_url, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    RETURNING id;
                    """,
                    (
                        topic, outline, draft, final_content,
                        tone, seo_keywords, image_url, vec_literal,
                    ),
                )
                new_id = cur.fetchone()[0]
    finally:
        conn.close()
    return new_id


def get_trusted_sources() -> list[dict]:
    """trusted_sources 목록을 [{domain, name, category}, ...] 로 반환한다.

    DB 접속/조회에 실패하면 빈 리스트를 반환한다(검색 자체는 계속 동작하도록).
    """
    try:
        conn = get_connection()
    except Exception:  # noqa: BLE001 - DB 미가동 시 신뢰 소스 없이 진행
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT domain, name, category FROM trusted_sources "
                "WHERE domain IS NOT NULL AND domain <> '';"
            )
            rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        return []
    finally:
        conn.close()

    return [{"domain": r[0], "name": r[1], "category": r[2]} for r in rows]


def find_cached_search(
    query: str,
    threshold: float = SEARCH_CACHE_SIMILARITY,
    ttl_hours: int = SEARCH_CACHE_TTL_HOURS,
):
    """search_cache 에서 query 와 (거의) 동일한 최근 검색 결과를 찾아 반환한다.

    검색어가 정확히 같거나 임베딩 유사도가 threshold 이상이고,
    캐시가 ttl_hours 이내면 결과 리스트([{title,url,snippet}, ...])를,
    없으면 None 을 반환한다.
    """
    vec_literal = _to_vector_literal(embed_text(query))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT query,
                       results_json,
                       1 - (embedding <=> %(v)s::vector) AS similarity
                FROM search_cache
                WHERE embedding IS NOT NULL
                  AND created_at > now() - make_interval(hours => %(ttl)s)
                ORDER BY embedding <=> %(v)s::vector
                LIMIT 1;
                """,
                {"v": vec_literal, "ttl": ttl_hours},
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    cached_query, results_json, similarity = row[0], row[1], float(row[2])
    if cached_query != query and similarity < threshold:
        return None
    try:
        return json.loads(results_json) if results_json else []
    except (TypeError, ValueError):
        return None


def save_search_cache(query: str, results: list[dict], embedding=None) -> int:
    """검색 결과를 search_cache 에 저장하고 새 id 를 반환한다."""
    vec_literal = _to_vector_literal(embedding) if embedding is not None else None
    payload = json.dumps(results, ensure_ascii=False)

    conn = get_connection()
    try:
        with conn:  # 예외 없이 끝나면 커밋
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO search_cache (query, results_json, embedding)
                    VALUES (%s, %s, %s::vector)
                    RETURNING id;
                    """,
                    (query, payload, vec_literal),
                )
                new_id = cur.fetchone()[0]
    finally:
        conn.close()
    return new_id


def _verify() -> None:
    """생성 결과를 information_schema / 카탈로그 조회로 확인해 출력한다."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            print("vector 확장:", "설치됨" if cur.fetchone() else "없음")

            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('trusted_sources', 'blog_posts', 'search_cache')
                ORDER BY table_name;
                """
            )
            tables = [r[0] for r in cur.fetchall()]
            print("생성된 테이블:", tables)

            cur.execute("SELECT count(*) FROM trusted_sources;")
            print("trusted_sources 행 수:", cur.fetchone()[0])

            cur.execute(
                "SELECT id, domain, name, category FROM trusted_sources ORDER BY id;"
            )
            for row in cur.fetchall():
                print("  ", row)

            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname LIKE '%embedding%'
                ORDER BY indexname;
                """
            )
            print("임베딩 인덱스:", [r[0] for r in cur.fetchall()])
    finally:
        conn.close()


if __name__ == "__main__":
    if not os.environ.get("DB_HOST"):
        print("DB 접속 정보가 .env 에 없습니다. (DB_HOST 등)")
        raise SystemExit(1)

    init_db()
    print("스키마 초기화 완료\n")
    _verify()
