-- ex-day PoC（Issue #1）：次元の違うモデルを同じテーブルに入れられるようにする
-- 02_schema.sql は384次元（multilingual-e5-small）に固定していた。
-- 日本語向けの Ruri v3（256〜768次元）等と比べるため、次元を固定しない vector 型に変える。
--
-- ・次元を固定しない列には HNSW インデックスを張れないため、インデックスは外す。
--   件数が少ない比較では、インデックスなしでも結果は同じ（全件を比べるだけ）。
--   件数を増やした速さの検証では、モデルを決めてから、そのモデルの次元でインデックスを張る。
-- ・何度流しても壊れない。

DROP INDEX IF EXISTS poc.subject_embedding_hnsw;
DROP INDEX IF EXISTS poc.discovery_embedding_hnsw;

ALTER TABLE poc.subject_embedding   ALTER COLUMN embedding TYPE vector;
ALTER TABLE poc.discovery_embedding ALTER COLUMN embedding TYPE vector;
