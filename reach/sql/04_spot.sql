-- reach/sql/04_spot.sql
-- 寄り道の提案（Issue #6）で、駅のまわりと突き合わせる「場所」。本番の Discovery ではない。
--   source = 'wikidata'：Wikidata（CC0）から取った神社・寺・公園・博物館・滝・湖など。仮の Discovery として使う
--   source = 'sample'  ：vector/sample_data.json の Discovery（中身が濃い）。座標は手で付けた概略
-- 埋め込みは load_spots.py が入れる。次元はモデルで違うので固定しない（全件をなめて比べる。4万件なら十分速い）。

CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS reach.spot;
CREATE TABLE reach.spot (
  spot_id     serial PRIMARY KEY,
  source      text NOT NULL CHECK (source IN ('wikidata', 'sample')),
  ext_id      text NOT NULL,          -- Wikidata の Q番号、または sample の id
  name        text NOT NULL,
  description text,
  kinds       text,                   -- 種類（神社、公園…）。複数は「、」区切り
  doc         text NOT NULL,          -- 埋め込みに使った文章
  model       text,                   -- 埋め込みのモデル
  embedding   vector,
  geom        geometry(Point, 4326) NOT NULL,
  UNIQUE (source, ext_id)
);

CREATE INDEX spot_geog_idx ON reach.spot USING gist ((geom::geography));
