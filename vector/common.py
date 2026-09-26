"""ex-day PoC（Issue #1）：共通の処理（DB接続・埋め込みの作成）"""
import hashlib
import os
import unicodedata

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

DSN = os.environ.get("EXDAY_DSN", "postgresql://postgres:postgres@127.0.0.1:5432/ex_day_poc")

# 使うモデル。環境変数 EXDAY_MODEL で切り替える（compare.py では複数をまとめて比べる）。
MODEL = os.environ.get("EXDAY_MODEL", "intfloat/multilingual-e5-small")

# モデルごとの「先頭に付ける決まり」（query：検索の問い・Subjectの名前、passage：検索される文章）
# モデルによって決まりが違う。ここにないモデルは付けない。
MODELS = {
    # 多言語（Microsoft）
    "intfloat/multilingual-e5-small": {"query": "query: ", "passage": "passage: ", "note": "多言語・384次元"},
    "intfloat/multilingual-e5-base": {"query": "query: ", "passage": "passage: ", "note": "多言語・768次元"},
    "intfloat/multilingual-e5-large": {"query": "query: ", "passage": "passage: ", "note": "多言語・1024次元"},
    # 日本語向け（名古屋大学 Ruri v3）
    "cl-nagoya/ruri-v3-30m": {"query": "検索クエリ: ", "passage": "検索文書: ", "note": "日本語・256次元"},
    "cl-nagoya/ruri-v3-70m": {"query": "検索クエリ: ", "passage": "検索文書: ", "note": "日本語・384次元"},
    "cl-nagoya/ruri-v3-130m": {"query": "検索クエリ: ", "passage": "検索文書: ", "note": "日本語・512次元"},
    "cl-nagoya/ruri-v3-310m": {"query": "検索クエリ: ", "passage": "検索文書: ", "note": "日本語・768次元"},
    # 比較用の基準（文字の重なりだけ。意味は分からない）
    "ngram-baseline": {"query": "", "passage": "", "note": "基準・384次元"},
}

NGRAM_DIM = 384


def connect():
    conn = psycopg.connect(DSN, autocommit=True)
    register_vector(conn)
    return conn


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Embedder:
    """文章をベクトルにする。次元はモデルごとに違ってよい（テーブルは次元を固定していない）。"""

    def __init__(self, model_name: str = None):
        self.model_name = model_name or MODEL
        self.prefix = MODELS.get(self.model_name, {"query": "", "passage": ""})
        if self.model_name == "ngram-baseline":
            self._model = None
            self.dim = NGRAM_DIM
        else:
            from sentence_transformers import SentenceTransformer  # 読み込みに時間がかかるため、使うときだけ読む

            self._model = SentenceTransformer(self.model_name)
            self.dim = self._model.get_sentence_embedding_dimension()

    def query(self, texts):
        return self._encode([self.prefix["query"] + t for t in texts])

    def passage(self, texts):
        return self._encode([self.prefix["passage"] + t for t in texts])

    def _encode(self, texts):
        if self._model is None:
            return np.array([_ngram_vector(t) for t in texts])
        return self._model.encode(texts, normalize_embeddings=True)


def _ngram_vector(text: str) -> np.ndarray:
    """比較用の基準：文字の2-gramをハッシュで振り分ける。文字が重なれば近く、意味は考慮しない。"""
    v = np.zeros(NGRAM_DIM, dtype=np.float32)
    t = text.replace(" ", "")
    grams = [t[i : i + 2] for i in range(len(t) - 1)] or [t]
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        v[h % NGRAM_DIM] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


def normalize(name: str) -> str:
    """完全一致の比較用に、表記をそろえる（全角・半角、大文字・小文字、空白）。"""
    return unicodedata.normalize("NFKC", name).lower().replace(" ", "")
