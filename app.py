"""
대전 지역 블로그 생성기 - Streamlit 프론트엔드

실행:
  streamlit run app.py
"""

import hashlib
import html as html_lib
import os
import re
import sys

import streamlit as st

# src/ 를 import 경로에 추가
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import blog_agent  # noqa: E402  (경로 추가 후 import)

st.set_page_config(page_title="대전 블로그 생성기", page_icon="📝")

st.title("📝 대전 지역 블로그 생성기")
st.caption(
    "리서치 → 아웃라인 → 초안 순으로 블로그 글을 만들어 줍니다. "
    "지역 정보가 필요한 주제는 자동으로 **대전**을 맥락으로 삼고, "
    "이미 비슷한 글이 있으면 카드로 보여 줍니다."
)

# 이전 실행 결과를 담는 세션 키
RESULT_KEYS = (
    "topic", "mode", "similar_posts", "final_content",
    "research", "sources", "outline", "draft", "post_id",
    "tone", "seo_keywords", "seo_report",
)

TONE_OPTIONS = ["정보성", "캐주얼", "리뷰형", "전문적"]

# 카드 UI 스타일 — 상단 70% 이미지 / 하단 30% 내용
CARD_CSS = """
<style>
.post-card {
  border: 1px solid rgba(128,128,128,.35);
  border-radius: 14px;
  overflow: hidden;
  height: 360px;
  display: flex;
  flex-direction: column;
  margin-bottom: 10px;
}
.post-card-img {
  flex: 0 0 70%;
  background-size: cover;
  background-position: center;
  background-color: rgba(128,128,128,.15);
}
.post-card-body {
  flex: 0 0 30%;
  padding: 10px 12px;
  overflow: hidden;
}
.post-card-title {
  font-weight: 700;
  font-size: .92rem;
  margin-bottom: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.post-card-meta { font-size: .72rem; opacity: .6; margin-bottom: 5px; }
.post-card-text {
  font-size: .78rem;
  line-height: 1.35;
  opacity: .85;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
"""


def _plain_excerpt(md: str, n: int = 160) -> str:
    """마크다운에서 대략적인 평문 발췌를 만든다."""
    t = md or ""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)          # 이미지 제거
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)      # 링크 -> 텍스트
    t = re.sub(r"[#>*`_~|-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _card_image_url(post: dict) -> str:
    """글 본문의 첫 이미지 URL, 없으면 주제 기반 대체 이미지."""
    m = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", post.get("final_content") or "")
    if m:
        return m.group(1)
    seed = hashlib.md5((post.get("topic") or "post").encode("utf-8")).hexdigest()[:12]
    return f"https://picsum.photos/seed/{seed}/600/400"


def _render_post_cards(posts: list[dict]) -> None:
    """유사 글을 카드 그리드로 표시 (상단 70% 이미지 / 하단 30% 내용)."""
    if not posts:
        return
    st.markdown(CARD_CSS, unsafe_allow_html=True)

    per_row = 3
    for i in range(0, len(posts), per_row):
        row = posts[i:i + per_row]
        cols = st.columns(per_row)
        for col, post in zip(cols, row):
            with col:
                meta_bits = [f"유사도 {post['similarity']:.2f}"]
                if post.get("tone"):
                    meta_bits.append(post["tone"])
                if post.get("created_at"):
                    meta_bits.append(str(post["created_at"])[:10])
                card = f"""
<div class="post-card">
  <div class="post-card-img" style="background-image:url('{_card_image_url(post)}')"></div>
  <div class="post-card-body">
    <div class="post-card-title">{html_lib.escape(post.get('topic') or '')}</div>
    <div class="post-card-meta">{html_lib.escape(' · '.join(meta_bits))}</div>
    <div class="post-card-text">{html_lib.escape(_plain_excerpt(post.get('final_content') or ''))}</div>
  </div>
</div>
"""
                st.markdown(card, unsafe_allow_html=True)
                with st.expander("전체 글 보기"):
                    st.markdown(post.get("final_content") or "")
                    st.download_button(
                        "📥 .md 다운로드",
                        data=post.get("final_content") or "",
                        file_name=f"{blog_agent.slugify(post.get('topic') or 'blog')}.md",
                        mime="text/markdown",
                        key=f"dl_{post.get('id')}",
                    )


def _render_seo_check() -> None:
    """SEO 키워드 체크 섹션."""
    report = st.session_state.get("seo_report") or {}
    if not report:
        return
    st.subheader("SEO 키워드 체크")
    st.caption("입력한 키워드가 본문에 등장한 횟수 (권장: 각 3~5회)")
    cols = st.columns(len(report))
    for col, (kw, cnt) in zip(cols, report.items()):
        delta = "적정" if 3 <= cnt <= 5 else ("부족" if cnt < 3 else "과다")
        col.metric(kw, f"{cnt}회", delta, delta_color="off")


topic = st.text_input("블로그 주제", value="대전 성심당 빵집 추천")

col1, col2 = st.columns(2)
with col1:
    tone = st.selectbox("톤앤매너", TONE_OPTIONS, index=0)
with col2:
    seo_raw = st.text_input(
        "SEO 키워드 (선택 · 쉼표로 구분)", value="", placeholder="예: 대전 맛집, 성심당"
    )

run = st.button("블로그 생성", type="primary")

if run and topic.strip():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY가 없습니다. 프로젝트 루트의 .env 파일을 확인하세요.")
        st.stop()

    for key in RESULT_KEYS:
        st.session_state.pop(key, None)

    t = topic.strip()
    st.session_state["topic"] = t
    st.session_state["tone"] = tone
    st.session_state["seo_keywords"] = seo_raw

    # --- 0단계: 임베딩 유사도로 기존 글 확인 ---
    with st.spinner("기존 글 확인 중 (임베딩 유사도 검색)..."):
        similar = blog_agent.find_similar_posts(t)

    if similar:
        st.session_state["mode"] = "existing"
        st.session_state["similar_posts"] = similar
        # SEO 체크는 가장 유사한 글 기준
        st.session_state["final_content"] = similar[0]["final_content"]
        st.session_state["seo_report"] = blog_agent.count_keyword_occurrences(
            similar[0]["final_content"], seo_raw
        )
    else:
        st.session_state["mode"] = "new"

        # --- 1단계: 리서치 ---
        with st.status("1/3 · 리서치 중 (웹 검색)...", expanded=True) as status:
            searched: list[str] = []

            def _on_search(q: str) -> None:
                searched.append(q)
                st.write(f"🔎 검색: `{q}`")

            notes, sources = blog_agent.research(t, on_search=_on_search)
            st.session_state["research"] = notes
            st.session_state["sources"] = sources
            status.update(label="1/3 · 리서치 완료", state="complete", expanded=False)

        # --- 2단계: 아웃라인 ---
        with st.status("2/3 · 아웃라인 작성 중...", expanded=False) as status:
            outline = blog_agent.make_outline(t, notes)
            st.session_state["outline"] = outline
            status.update(label="2/3 · 아웃라인 완료", state="complete")

        # --- 3단계: 초안 (톤 + SEO 키워드 반영) ---
        with st.status(f"3/3 · 블로그 초안 작성 중 (톤: {tone})...", expanded=False) as status:
            draft = blog_agent.write_draft(
                t, outline, notes, tone=tone, seo_keywords=seo_raw
            )
            st.session_state["draft"] = draft
            st.session_state["final_content"] = draft
            st.session_state["seo_report"] = blog_agent.count_keyword_occurrences(
                draft, seo_raw
            )
            status.update(label="3/3 · 초안 완료", state="complete")

        # --- 4단계: 임베딩 + 톤 + SEO 키워드와 함께 DB 저장 ---
        with st.status("저장 중 (임베딩 생성 + DB 저장)...", expanded=False) as status:
            post_id = blog_agent.persist_blog(
                t, outline, draft, tone=tone, seo_keywords=seo_raw
            )
            st.session_state["post_id"] = post_id
            status.update(label=f"blog_posts #{post_id} 저장 완료", state="complete")


# --- 결과 표시 (생성 직후 & 재실행 시 모두) ---
mode = st.session_state.get("mode")
topic_done = st.session_state.get("topic", "blog")

if mode == "existing":
    posts = st.session_state.get("similar_posts", [])
    st.divider()
    st.warning(
        f"이미 비슷한 글이 {len(posts)}건 있습니다. 새로 생성하지 않고 기존 글을 보여드립니다."
    )
    st.caption(f"입력한 주제: {topic_done}")
    _render_post_cards(posts)
    _render_seo_check()

elif mode == "new":
    st.divider()
    st.success(
        f"새 글을 생성했습니다. (톤: {st.session_state.get('tone')} · "
        f"blog_posts #{st.session_state.get('post_id')} 저장됨)"
    )

    st.subheader("1. 리서치 결과")
    st.markdown(st.session_state["research"])

    sources = st.session_state.get("sources", [])
    if sources:
        st.markdown("**검색된 출처**")
        for s in sources:
            label = s.get("title") or s.get("url")
            if s.get("url"):
                st.markdown(f"- [{label}]({s['url']})")

    st.subheader("2. 아웃라인")
    st.markdown(st.session_state["outline"])

    st.subheader("3. 블로그 초안")
    st.markdown(st.session_state["draft"])

    _render_seo_check()

    st.download_button(
        "📥 .md 파일로 다운로드",
        data=st.session_state["draft"],
        file_name=f"{blog_agent.slugify(topic_done)}.md",
        mime="text/markdown",
    )
