-- reach/sql/01_schema.sql
-- ex-day PoC（Issue #5）：寄り道できる駅のリストを、国土数値情報 N02（鉄道）と路線ごとの表定速度で粗く出す。
-- 本番のドメイン設計には持ち込まない、検証用の物理テーブル。
-- 流す順：01_schema.sql → load_n02.py → 02_build_graph.sql → 03_reachable.sql

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting CASCADE;

DROP SCHEMA IF EXISTS reach CASCADE;
CREATE SCHEMA reach;

-- 取り込んだまま（load_n02.py が入れる）。座標系は JGD2011（EPSG:6668）のまま。
CREATE TABLE reach.n02_station (
  id            serial PRIMARY KEY,
  rail_class    text NOT NULL,   -- N02_001 鉄道区分（11 普通鉄道JR、12 普通鉄道、21 軌道 等）
  operator_type text NOT NULL,   -- N02_002 事業者種別（1 新幹線、2 JR在来線、3 公営、4 民営、5 第三セクター）
  line_name     text NOT NULL,   -- N02_003 路線名
  operator      text NOT NULL,   -- N02_004 運営会社
  station_name  text NOT NULL,   -- N02_005 駅名
  station_code  text NOT NULL,   -- N02_005c 駅コード
  group_code    text NOT NULL,   -- N02_005g グループコード（乗換できる駅のまとまり）
  geom          geometry(LineString, 6668) NOT NULL  -- 駅はホームの範囲の線
);

CREATE TABLE reach.n02_section (
  id            serial PRIMARY KEY,
  rail_class    text NOT NULL,
  operator_type text NOT NULL,
  line_name     text NOT NULL,
  operator      text NOT NULL,
  geom          geometry(LineString, 6668) NOT NULL  -- 駅の中と駅の間で区切られている
);

-- 路線ごとの設定。速度は人が直す。値を変えたら 02_build_graph.sql の「辺の所要時間」の部分を流し直す。
CREATE TABLE reach.line (
  line_id       serial PRIMARY KEY,
  operator      text NOT NULL,
  line_name     text NOT NULL,
  rail_class    text NOT NULL,
  operator_type text NOT NULL,
  service_type  text NOT NULL,   -- shinkansen／conventional／subway／tram／monorail／agt／cable／other
  speed_kmh     numeric NOT NULL,
  UNIQUE (operator, line_name)
);

-- 区間ごとの速度の上書き（将来用）。同じ路線の2駅の間を、別の速度にする。
-- 例：京急本線の横浜〜浦賀を遅くする。駅は駅名で指定し、2駅の間の最短の経路上の辺に当てる。
CREATE TABLE reach.speed_override (
  id            serial PRIMARY KEY,
  operator      text NOT NULL,
  line_name     text NOT NULL,
  from_station  text NOT NULL,
  to_station    text NOT NULL,
  speed_kmh     numeric NOT NULL,
  source        text NOT NULL    -- 値の出どころ（時刻表から計算、聞いた話、等）
);

-- グラフの点。路線ごとに分ける（別の路線の線路が触れている場所で、乗換なしに乗り移れないようにする）。
CREATE TABLE reach.node (
  node_id  bigserial PRIMARY KEY,
  line_id  int NOT NULL REFERENCES reach.line,
  geom     geometry(Point, 6668) NOT NULL,
  UNIQUE (line_id, geom)
);

-- グラフの辺。pgRouting が読む。cost・reverse_cost は分。
CREATE TABLE reach.edge (
  id            bigserial PRIMARY KEY,
  kind          text NOT NULL CHECK (kind IN ('ride', 'transfer')),
  line_id       int REFERENCES reach.line,        -- 乗換は NULL
  section_id    int REFERENCES reach.n02_section,  -- 乗換は NULL
  source        bigint NOT NULL REFERENCES reach.node,
  target        bigint NOT NULL REFERENCES reach.node,
  length_m      double precision,
  speed_kmh     numeric,
  cost          double precision NOT NULL,
  reverse_cost  double precision NOT NULL,
  geom          geometry(LineString, 6668)
);

-- 駅。station_code ごと（同じ駅でも路線ごとに別の行）。
CREATE TABLE reach.station (
  station_code  text PRIMARY KEY,
  station_name  text NOT NULL,
  group_code    text NOT NULL,
  line_id       int NOT NULL REFERENCES reach.line,
  edge_id       bigint REFERENCES reach.edge,       -- 駅のホームにあたる辺
  node_a        bigint REFERENCES reach.node,       -- ホームの両端
  node_b        bigint REFERENCES reach.node,
  geom          geometry(Point, 4326)               -- ホームの中ほど。#6 で Discovery と突き合わせる
);
