"""
로컬 한국어 임베딩 함수

- 모델: jhgan/ko-sroberta-multitask (출력 768차원 → DB 스키마의 vector(768)과 일치)
- 모델은 최초 호출 시 한 번만 로드되어 전역 변수에 캐싱된다.

사용:
  from embeddings import embed_text
  vec = embed_text("대전 성심당 빵집 추천")   # -> list[float] (길이 768)
"""

from __future__ import annotations

MODEL_NAME = "jhgan/ko-sroberta-multitask"
EMBEDDING_DIM = 768

# 전역 캐시: 모델을 프로세스 내에서 단 한 번만 로드한다.
_model = None


def get_model():
    """SentenceTransformer 모델을 반환한다. 최초 1회만 로드하고 이후 캐시를 재사용."""
    global _model
    if _model is None:
        # import 도 최초 로드 시점으로 미뤄 두어, 임베딩을 안 쓰는 코드의 시작을 느리게 하지 않는다.
        from sentence_transformers import SentenceTransformer

        print(f"[embeddings] 모델 로딩: {MODEL_NAME} ...")
        _model = SentenceTransformer(MODEL_NAME)
        print("[embeddings] 모델 로딩 완료")
    return _model


def embed_text(text: str) -> list[float]:
    """문장 하나를 768차원 임베딩 벡터(list[float])로 변환한다."""
    vec = get_model().encode(text, normalize_embeddings=False)
    return vec.astype(float).tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """여러 문장을 한 번에 임베딩한다. (배치가 개별 호출보다 빠름)"""
    vecs = get_model().encode(list(texts), normalize_embeddings=False)
    return [v.astype(float).tolist() for v in vecs]


if __name__ == "__main__":
    # 4) 차원 검증
    sample = "대전 성심당 빵집 추천"
    vec = embed_text(sample)

    print(f"입력 문장   : {sample}")
    print(f"벡터 타입   : {type(vec).__name__}, 원소 타입: {type(vec[0]).__name__}")
    print(f"벡터 차원   : {len(vec)}")
    print(f"앞 5개 값   : {vec[:5]}")

    assert isinstance(vec, list), "반환값이 list 가 아님"
    assert all(isinstance(x, float) for x in vec[:10]), "원소가 float 이 아님"
    assert len(vec) == EMBEDDING_DIM, f"차원이 {EMBEDDING_DIM} 이 아님: {len(vec)}"

    # 캐시 동작 확인: 두 번째 호출은 모델을 다시 로드하지 않아야 한다.
    _first = _model
    _ = embed_text("두 번째 호출")
    assert _model is _first, "모델이 재로딩됨 (캐시 실패)"

    print("\n[OK] 768차원 벡터 정상 출력, 전역 모델 캐시 동작 확인 (DB vector(768) 호환)")
