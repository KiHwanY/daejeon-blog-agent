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
from contextlib import contextmanager

import anthropic
import psycopg2
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
    "tone", "seo_keywords", "seo_report", "image_url",
    "search_retried", "critique_passed", "critique_feedback", "was_rewritten",
    "research_grounded", "research_reason",
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
    """카드 이미지 URL: Pexels image_url → 본문 첫 이미지 → 주제 기반 플레이스홀더."""
    if post.get("image_url"):
        return post["image_url"]
    m = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", post.get("final_content") or "")
    if m:
        return m.group(1)
    seed = hashlib.md5((post.get("topic") or "post").encode("utf-8")).hexdigest()[:12]
    return f"https://picsum.photos/seed/{seed}/600/400"


def _render_grounding_warning(src: dict) -> None:
    """리서치 근거가 부족한 결과에 눈에 띄는 경고 배너를 표시한다."""
    if src.get("research_grounded") is not False:
        return
    reason = (src.get("research_reason") or "").strip()
    st.error(
        "⚠️ **이 주제는 온라인에서 확인 가능한 구체적 정보가 부족합니다.** "
        "일반적인 내용 위주로 작성되었으니 **사실 확인이 필요합니다.**"
        + (f"\n\n> {reason}" if reason else "")
    )


def _render_agent_log(src: dict) -> None:
    """에이전트 자체 판단 로그를 접이식으로 표시한다 (검색 재시도 / 자체 검토 / 재작성)."""
    has_any = any(
        src.get(k) is not None
        for k in ("search_retried", "critique_passed", "was_rewritten",
                  "critique_feedback", "research_grounded")
    )
    if not has_any:
        return

    retried = bool(src.get("search_retried"))
    passed = src.get("critique_passed")
    rewritten = bool(src.get("was_rewritten"))
    feedback = src.get("critique_feedback") or ""

    if passed is True and not rewritten:
        summary = "✅ 자체 검토 통과"
    elif rewritten:
        summary = "🔁 자체 검토 후 재작성함"
    elif passed is False:
        summary = "⚠️ 자체 검토 미통과"
    else:
        summary = "자체 판단 로그"

    grounded = src.get("research_grounded")

    with st.expander(f"🧠 에이전트 판단 로그 — {summary}"):
        if grounded is not None:
            gtext = "충분" if grounded else "부족 ⚠️ (사실 확인 필요)"
            st.markdown(f"- **리서치 근거**: {gtext}")
            if grounded is False and src.get("research_reason"):
                st.caption(str(src["research_reason"]))
        st.markdown(f"- **검색 재시도**: {'예 (결과 부족으로 새 키워드 재검색)' if retried else '아니오'}")
        if passed is None:
            st.markdown("- **자체 검토**: 기록 없음")
        else:
            st.markdown(f"- **자체 검토 결과**: {'통과(pass)' if passed else '미통과(fail)'}")
        st.markdown(f"- **재작성 여부**: {'예 (피드백 반영해 1회 재작성)' if rewritten else '아니오'}")
        if feedback:
            main_fb, _, auto_note = feedback.partition("⚠️[자동 점검]")
            st.markdown("- **검토 피드백**:")
            st.info(main_fb.strip())
            if auto_note.strip():
                st.warning("⚠️ 자동 점검: " + auto_note.strip())


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
                _render_agent_log(post)


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


def _friendly_error(e: Exception) -> str:
    """예외를 사용자가 이해할 수 있는 한국어 안내 문구로 바꾼다."""
    if isinstance(e, anthropic.AuthenticationError):
        return (
            "Anthropic API 키가 유효하지 않습니다. "
            "프로젝트 루트 `.env` 의 `ANTHROPIC_API_KEY` 값을 확인하세요."
        )
    if isinstance(e, anthropic.PermissionDeniedError):
        return (
            "이 API 키로는 해당 모델을 사용할 수 없습니다. "
            "Anthropic 콘솔에서 결제·권한 상태를 확인하세요."
        )
    if isinstance(e, anthropic.RateLimitError):
        return (
            "Anthropic API 사용량 한도(rate limit)에 걸렸습니다. "
            "잠시 후 다시 시도하거나 콘솔에서 한도를 확인하세요."
        )
    if isinstance(e, anthropic.APIConnectionError):
        return "Anthropic API 에 연결하지 못했습니다. 네트워크 상태를 확인하세요."
    if isinstance(e, anthropic.APIStatusError):
        return (
            f"Anthropic API 오류가 발생했습니다 (HTTP {e.status_code}). "
            "잠시 후 다시 시도하세요."
        )
    if isinstance(e, anthropic.APIError):
        return f"Anthropic API 호출 중 오류가 발생했습니다: {e}"
    if isinstance(e, psycopg2.OperationalError):
        return (
            "데이터베이스에 연결하지 못했습니다. Docker 컨테이너(`daejeon-blog-db`) 실행 여부와 "
            "`.env` 의 `DB_*` 설정을 확인하세요."
        )
    if isinstance(e, psycopg2.Error):
        return f"데이터베이스 오류가 발생했습니다: {e}"
    return f"예상치 못한 오류가 발생했습니다: {e}"


@contextmanager
def _guard():
    """블록 내부에서 발생한 예외를 안내 문구로 표시하고 실행을 멈춘다."""
    try:
        yield
    except Exception as e:  # noqa: BLE001 - 사용자에게 보여줄 최종 방어선
        st.error(_friendly_error(e))
        st.stop()


# 파이프라인 단계 → (진행 중 라벨, 완료 라벨)
_STAGE_LABELS = {
    "similar": ("기존 글 확인 중 (임베딩 유사도 검색)...", "기존 글 확인 완료"),
    "research": ("1/3 · 리서치 중 (웹 검색)...", "1/3 · 리서치 완료"),
    "outline": ("2/3 · 아웃라인 작성 중...", "2/3 · 아웃라인 완료"),
    "grounding": ("리서치 근거 확인 중...", "리서치 근거 확인 완료"),
    "draft": ("3/3 · 블로그 초안 작성 중...", "3/3 · 초안 완료"),
    "critique": ("자체 검토 중 (사실·구조·톤·SEO)...", "자체 검토 완료"),
    "rewrite": ("재작성 중 (검토 피드백 반영)...", "재작성 완료"),
    "image": ("이미지 검색 중 (Pexels)...", "이미지 준비 완료"),
    "save": ("저장 중 (임베딩 생성 + DB 저장)...", "저장 완료"),
}


def _run_pipeline(
    topic_text: str, tone: str, seo_raw: str, abort_on_weak_research: bool = False
) -> dict:
    """blog_agent.generate_blog 를 단계별 st.status UI 콜백과 함께 호출한다."""
    widgets: dict = {}

    def on_stage(ev: dict) -> None:
        stage, state = ev["stage"], ev["state"]
        start_label, done_label = _STAGE_LABELS[stage]
        if state == "start":
            if stage == "draft" and ev.get("tone"):
                start_label = f"3/3 · 블로그 초안 작성 중 (톤: {ev['tone']})..."
            widgets[stage] = st.status(start_label, expanded=(stage == "research"))
        elif state == "done":
            widget = widgets.get(stage)
            if widget is None:
                return
            if stage == "save" and ev.get("post_id"):
                done_label = f"blog_posts #{ev['post_id']} 저장 완료"
            elif stage == "image" and not ev.get("image_url"):
                done_label = "이미지 없음 (플레이스홀더 사용)"
            elif stage == "critique":
                done_label = (
                    "자체 검토 통과" if ev.get("passed") else "자체 검토 미통과 → 재작성"
                )
            elif stage == "grounding" and ev.get("grounded") is False:
                done_label = "리서치 근거 부족 ⚠️"
            widget.update(label=done_label, state="complete", expanded=False)

    def on_search(query: str) -> None:
        (widgets.get("research") or st).write(f"🔎 검색: `{query}`")

    return blog_agent.generate_blog(
        topic_text,
        on_search=on_search,
        on_stage=on_stage,
        tone=tone,
        seo_keywords=seo_raw,
        abort_on_weak_research=abort_on_weak_research,
    )


def _stash_result(result: dict) -> None:
    """generate_blog 결과를 결과 표시 섹션이 읽는 session_state 키로 옮긴다."""
    if result.get("aborted"):
        st.session_state["mode"] = "aborted"
        st.session_state["research_grounded"] = result.get("research_grounded")
        st.session_state["research_reason"] = result.get("research_reason")
        return

    if result["existing"]:
        st.session_state["mode"] = "existing"
        st.session_state["similar_posts"] = result["similar_posts"]
        st.session_state["final_content"] = result["final_content"]
        st.session_state["seo_report"] = result["seo_report"]
        return

    st.session_state["mode"] = "new"
    for key in ("research", "sources", "outline", "draft",
                "final_content", "post_id", "seo_report", "image_url",
                "search_retried", "critique_passed", "critique_feedback",
                "was_rewritten", "research_grounded", "research_reason"):
        st.session_state[key] = result[key]


topic = st.text_input("블로그 주제", value="대전 성심당 빵집 추천")

col1, col2 = st.columns(2)
with col1:
    tone = st.selectbox("톤앤매너", TONE_OPTIONS, index=0)
with col2:
    seo_raw = st.text_input(
        "SEO 키워드 (선택 · 쉼표로 구분)", value="", placeholder="예: 대전 맛집, 성심당"
    )

abort_weak = st.checkbox(
    "리서치 근거 부족 시 생성 중단",
    value=False,
    help=(
        "체크 시(옵션 A): 온라인에서 확인 가능한 구체 정보가 부족하다고 판단되면 "
        "초안을 만들지 않고 중단합니다. 해제 시(옵션 B, 기본): 경고와 함께 "
        "일반론 위주로 초안을 생성합니다."
    ),
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

    with _guard():
        result = _run_pipeline(t, tone, seo_raw, abort_on_weak_research=abort_weak)
        _stash_result(result)


# --- 결과 표시 (생성 직후 & 재실행 시 모두) ---
mode = st.session_state.get("mode")
topic_done = st.session_state.get("topic", "blog")

if mode == "aborted":
    st.divider()
    _render_grounding_warning(st.session_state)
    st.info(
        "리서치 근거가 부족해 초안 생성을 **중단**했습니다. "
        "주제를 더 구체적으로 바꾸거나 다른 주제로 시도해 보세요."
    )
    st.caption(f"입력한 주제: {topic_done}")

elif mode == "existing":
    posts = st.session_state.get("similar_posts", [])
    st.divider()
    _render_grounding_warning(posts[0] if posts else {})
    st.warning(
        f"이미 비슷한 글이 {len(posts)}건 있습니다. 새로 생성하지 않고 기존 글을 보여드립니다."
    )
    st.caption(f"입력한 주제: {topic_done}")
    _render_post_cards(posts)
    _render_seo_check()

elif mode == "new":
    st.divider()
    _render_grounding_warning(st.session_state)
    st.success(
        f"새 글을 생성했습니다. (톤: {st.session_state.get('tone')} · "
        f"blog_posts #{st.session_state.get('post_id')} 저장됨)"
    )

    hero = _card_image_url({
        "image_url": st.session_state.get("image_url"),
        "final_content": st.session_state.get("draft"),
        "topic": topic_done,
    })
    st.image(hero, use_container_width=True)

    _render_agent_log(st.session_state)

    st.subheader("1. 리서치 결과")
    st.markdown(st.session_state["research"])

    sources = st.session_state.get("sources", [])
    if sources:
        st.markdown("**검색된 출처** (✅ = 대전 신뢰 소스)")
        for s in sources:
            label = s.get("title") or s.get("url")
            if s.get("url"):
                badge = f" ✅ {s['source_name']}" if s.get("trusted") else ""
                st.markdown(f"- [{label}]({s['url']}){badge}")

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
