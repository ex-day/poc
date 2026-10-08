-- reach/sql/06_walk.sql
-- 駅から場所までの歩きの道のりを出すための、道路のグラフ（Issue #23）。
-- データは国土数値情報 道路データ（N13、CC BY 4.0）。座標系は JGD2011（EPSG:6668）のまま。
-- 流す順：06_walk.sql → load_n13.py → 07_build_walk.sql → 08_walk_suggest.sql

DROP TABLE IF EXISTS reach.station_spot_walk, reach.spot_walk, reach.station_walk, reach.walk_edge, reach.walk_node, reach.n13_road CASCADE;

-- 取り込んだまま（load_n13.py が入れる）
CREATE TABLE reach.n13_road (
  id         serial PRIMARY KEY,
  kind       text,   -- N13_002 種別（1 が大半。公園・境内の中の細い道は 2 が多い）
  road_class text,   -- N13_003 道路分類
  state      text,   -- N13_004 道路状態
  layer      int,    -- N13_005 階層順（0 が地上。高架などは 1 以上）
  width      text,   -- N13_006 幅員区分
  toll       text,   -- N13_007 有料区分（2 が有料と見られる）
  geom       geometry(LineString, 6668) NOT NULL
);

-- 歩きのグラフの点：道路の線の端。交差点で線が切れているので、端どうしが同じ位置なら同じ点とみなす
CREATE TABLE reach.walk_node (
  node_id bigserial PRIMARY KEY,
  geom    geometry(Point, 6668) NOT NULL UNIQUE
);

-- 歩きのグラフの辺：道路の線1本が辺1本。cost は分（分速80m）
CREATE TABLE reach.walk_edge (
  id           bigserial PRIMARY KEY,
  road_id      int NOT NULL REFERENCES reach.n13_road,
  source       bigint NOT NULL REFERENCES reach.walk_node,
  target       bigint NOT NULL REFERENCES reach.walk_node,
  length_m     double precision NOT NULL,
  cost         double precision NOT NULL,
  reverse_cost double precision NOT NULL,
  geom         geometry(LineString, 6668) NOT NULL
);

-- 駅グループ・場所と、最寄りの道路の点の対応（直線でつなぐ距離も持つ）
CREATE TABLE reach.station_walk (
  group_code text PRIMARY KEY,
  node_id    bigint NOT NULL REFERENCES reach.walk_node,
  access_m   double precision NOT NULL
);
CREATE TABLE reach.spot_walk (
  spot_id  int PRIMARY KEY,
  node_id  bigint NOT NULL REFERENCES reach.walk_node,
  access_m double precision NOT NULL
);

-- 駅グループから、片道25分までに歩いて行ける場所と、歩く時間（分）。
-- 駅と場所の組で決まり、問い合わせ（出発・帰着・興味）によらないので、前もって計算しておく（08_walk_suggest.sql の最後で作る）
CREATE TABLE reach.station_spot_walk (
  group_code text NOT NULL,
  spot_id    int NOT NULL,
  walk_min   double precision NOT NULL,
  PRIMARY KEY (group_code, spot_id)
);
