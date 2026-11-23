from functools import lru_cache
from sentence_transformers import SentenceTransformer # 허깅페이스에서 모델을 불러올 때 사용하는 도구

MODEL_NAME = "BAAI/bge-m3"

@lru_cache(maxsize=1)
def get_embedding_model():
    """임베딩 모델 최초 1회만 로드"""
    return SentenceTransformer(MODEL_NAME)

def embeded_documents(texts: list[str]) -> list[list[float]]:
    """문서 청크들을 벡터로 변환"""
    model = get_embedding_model()

    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=True
    )

    return embeddings.tolist()

def embed_query(query: str) -> list[float]:
    """검색 질문을 벡터로 변환"""
    model = get_embedding_model()

    embedding = model.encode(
        query,
        normalize_embeddings=True
    )

    return embedding.tolist()