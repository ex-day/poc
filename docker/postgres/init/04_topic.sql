-- ex-day PoC（Issue #1）：話題（会話の集合）単位のベクトル
-- 話題ごとに「何について話しているか」（型：［場所］の［対象］を［観点］で話している）を1行で持ち、
-- その1行と、話題でわかってきたことをベクトル化する（#61「判断：話題ごとに『何について話しているか』を型で取る」）。
-- 何度流しても壊れない。

CREATE TABLE IF NOT EXISTS poc.topic (
  id            text PRIMARY KEY,
  discovery_id  text REFERENCES poc.discovery(id) ON DELETE CASCADE,
  place         text,          -- 場所（辞書の名前）
  target        text,          -- 対象
  viewpoint     text,          -- 観点
  about         text NOT NULL  -- 何について話しているか（1行）
);

CREATE TABLE IF NOT EXISTS poc.topic_finding (
  id        serial PRIMARY KEY,
  topic_id  text REFERENCES poc.topic(id) ON DELETE CASCADE,
  kind      text,
  text      text NOT NULL
);

-- variant：about（何についての1行だけ） / about_findings（1行＋わかってきたこと）
CREATE TABLE IF NOT EXISTS poc.topic_embedding (
  topic_id      text REFERENCES poc.topic(id) ON DELETE CASCADE,
  model         text NOT NULL,
  variant       text NOT NULL,
  source_text   text NOT NULL,
  content_hash  text NOT NULL,
  embedding     vector NOT NULL,
  PRIMARY KEY (topic_id, model, variant)
);
