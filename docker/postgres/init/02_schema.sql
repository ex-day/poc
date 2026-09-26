-- ex-day PoC（Issue #1）：ベクター検索の検証用テーブル
-- 本番のドメイン設計・論理Entity設計には持ち込まない検証用の物理テーブル。
-- 埋め込みは multilingual-e5-small（384次元）を前提とする。別のモデルに変える場合は vector(384) の次元を合わせる。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS poc;

-- 検索される側
CREATE TABLE IF NOT EXISTS poc.discovery (
  id            text PRIMARY KEY,
  name          text NOT NULL,
  spatial_type  text,          -- Point / Area / Route
  note          text,          -- 検証上のメモ（正解の意図など）
  geom          geography      -- #82 で使う（今は空でよい）
);

-- Subject（対象・観点・場所・出来事）
CREATE TABLE IF NOT EXISTS poc.subject (
  id               serial PRIMARY KEY,
  name             text NOT NULL,
  normalized_name  text NOT NULL UNIQUE,
  kind             text          -- target / viewpoint / place / event
);

CREATE TABLE IF NOT EXISTS poc.discovery_subject (
  discovery_id  text REFERENCES poc.discovery(id) ON DELETE CASCADE,
  subject_id    int  REFERENCES poc.subject(id) ON DELETE CASCADE,
  role          text,          -- 対象 / 観点
  PRIMARY KEY (discovery_id, subject_id)
);

-- わかってきたこと（ベクトル化する文章の材料）
CREATE TABLE IF NOT EXISTS poc.discovery_finding (
  id            serial PRIMARY KEY,
  discovery_id  text REFERENCES poc.discovery(id) ON DELETE CASCADE,
  kind          text,          -- theory（説） / value（価値）
  text          text NOT NULL
);

-- 埋め込み：Subject
CREATE TABLE IF NOT EXISTS poc.subject_embedding (
  subject_id    int  REFERENCES poc.subject(id) ON DELETE CASCADE,
  model         text NOT NULL,
  source_text   text NOT NULL,   -- 実際にベクトル化した文章
  content_hash  text NOT NULL,   -- 文章が変わったときだけ作り直すため
  embedding     vector(384) NOT NULL,
  PRIMARY KEY (subject_id, model)
);

-- 埋め込み：Discovery（何をベクトル化するかを variant で比べる）
--   name_subjects : 名前＋Subject
--   full          : 名前＋Subject＋わかってきたこと
CREATE TABLE IF NOT EXISTS poc.discovery_embedding (
  discovery_id  text REFERENCES poc.discovery(id) ON DELETE CASCADE,
  model         text NOT NULL,
  variant       text NOT NULL,
  source_text   text NOT NULL,
  content_hash  text NOT NULL,
  embedding     vector(384) NOT NULL,
  PRIMARY KEY (discovery_id, model, variant)
);

-- 近いものを速く探すためのインデックス（HNSW・コサイン距離）
-- 件数が少ないうちは使われなくても結果は同じ。件数を増やした検証で効果を見る。
CREATE INDEX IF NOT EXISTS subject_embedding_hnsw
  ON poc.subject_embedding USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS discovery_embedding_hnsw
  ON poc.discovery_embedding USING hnsw (embedding vector_cosine_ops);
