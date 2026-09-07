"""
PostgreSQL(pgvector) 연결 및 스키마 초기화

.env 의 DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD 를 사용한다.

사용:
  python src/db.py        # vector 확장 + 테이블 생성 + 기본 도메인 시딩 + 검증 출력
"""

import os

import psycopg2
from dotenv import load_dotenv

from embeddings import embed_text

# 프로젝트 루트(혹은 상위 경로)의 .env 를 읽어온다.
load_dotenv()

# 유사 글로 간주하는 코사인 유사도 하한
SIMILARITY_THRESHOLD = 0.85


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
        embedding     vector(768),
        created_at    TIMESTAMP DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        id           SERIAL PRIMARY KEY,
        query        TEXT,
        results_json TEXT,
        embedding    vector(768),
        created_at   TIMESTAMP DEFAULT now()
    );
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


def find_similar_post(topic: str, threshold: float = SIMILARITY_THRESHOLD):
    """topic 을 임베딩해 blog_posts 에서 코사인 거리(<=>)가 가장 가까운 글을 찾는다.

    유사도(= 1 - 코사인거리)가 threshold 이상이면
    {"topic", "final_content", "similarity"} 를, 그렇지 않으면 None 을 반환.
    """
    vec_literal = _to_vector_literal(embed_text(topic))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT topic,
                       final_content,
                       1 - (embedding <=> %(v)s::vector) AS similarity
                FROM blog_posts
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %(v)s::vector
                LIMIT 1;
                """,
                {"v": vec_literal},
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    similarity = float(row[2])
    if similarity < threshold:
        return None
    return {"topic": row[0], "final_content": row[1], "similarity": similarity}


def save_blog_post(topic, outline, draft, final_content, embedding=None) -> int:
    """완성된 글을 blog_posts 에 저장하고 새 id 를 반환한다."""
    vec_literal = _to_vector_literal(embedding) if embedding is not None else None

    conn = get_connection()
    try:
        with conn:  # 예외 없이 끝나면 커밋
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO blog_posts
                        (topic, outline, draft, final_content, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    RETURNING id;
                    """,
                    (topic, outline, draft, final_content, vec_literal),
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
    finally:
        conn.close()


if __name__ == "__main__":
    if not os.environ.get("DB_HOST"):
        print("DB 접속 정보가 .env 에 없습니다. (DB_HOST 등)")
        raise SystemExit(1)

    init_db()
    print("스키마 초기화 완료\n")
    _verify()
