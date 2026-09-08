"""image_url 이 비어 있는 기존 blog_posts 행에 Pexels 이미지를 채운다.

사용:
  python src/backfill_images.py             # 비어 있는 행 전부
  python src/backfill_images.py --limit 5   # 최대 5개만
  python src/backfill_images.py --dry-run   # 실제 UPDATE 없이 검색 결과만 출력
"""

import argparse

from config import PEXELS_API_KEY
from db import get_connection
from images import get_topic_image


def rows_missing_image(limit: int | None = None) -> list[tuple]:
    """image_url 이 NULL 이거나 빈 문자열인 (id, topic) 목록을 id 오름차순으로 반환."""
    sql = (
        "SELECT id, topic FROM blog_posts "
        "WHERE image_url IS NULL OR image_url = '' "
        "ORDER BY id"
    )
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def set_image_url(post_id: int, url: str) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE blog_posts SET image_url = %s WHERE id = %s",
                    (url, post_id),
                )
    finally:
        conn.close()


def backfill(limit: int | None = None, dry_run: bool = False) -> tuple[int, int]:
    """빈 행을 채우고 (채운 개수, 대상 개수) 를 반환한다."""
    rows = rows_missing_image(limit)
    filled = 0
    for post_id, topic in rows:
        url = get_topic_image(topic or "")
        if url:
            if not dry_run:
                set_image_url(post_id, url)
            filled += 1
            print(f"  #{post_id} {topic!r} -> {url}")
        else:
            print(f"  #{post_id} {topic!r} -> (이미지 없음, 건너뜀)")
    return filled, len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="처리할 최대 행 수")
    parser.add_argument("--dry-run", action="store_true", help="UPDATE 없이 검색만")
    args = parser.parse_args()

    if not PEXELS_API_KEY:
        print("PEXELS_API_KEY 가 .env 에 없습니다. 이미지 백필을 건너뜁니다.")
        raise SystemExit(1)

    print("image_url 이 비어 있는 blog_posts 행을 채웁니다...")
    filled, total = backfill(args.limit, dry_run=args.dry_run)
    verb = "채울 예정" if args.dry_run else "채움"
    print(f"\n완료: 대상 {total}건 중 {filled}건 {verb}")
