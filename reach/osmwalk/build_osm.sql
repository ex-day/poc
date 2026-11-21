-- reach/osmwalk/build_osm.sql
-- OSM の歩きのグラフ（load_osm_walk.py が入れたもの）に索引を付け、駅グループ・場所を最寄りの点に結び付ける（Issue #46）。
-- 結び付け方は 07_build_walk.sql（N13）と同じ（駅グループはホームの重心、300m まで）。何度流しても同じ結果になる。

TRUNCATE reach.spot_walk, reach.station_walk;

CREATE INDEX IF NOT EXISTS walk_node_geom_idx ON reach.walk_node USING gist (geom);
CREATE INDEX IF NOT EXISTS walk_edge_geom_idx ON reach.walk_edge USING gist (geom);
ANALYZE reach.walk_node;
ANALYZE reach.walk_edge;

-- 駅グループの代表点（ホームの中ほどの重心）から、いちばん近い道路の点。300m より遠ければ結び付けない（範囲の外の駅）
INSERT INTO reach.station_walk (group_code, node_id, access_m)
SELECT g.group_code, n.node_id, n.d
FROM (
  SELECT group_code, ST_Transform(ST_Centroid(ST_Collect(geom)), 6668) AS p
  FROM reach.station GROUP BY group_code
) g
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, ST_Transform(g.p, 4326)::geography) AS d
  FROM reach.walk_node w
  ORDER BY w.geom <-> g.p
  LIMIT 1
) n
WHERE n.d <= 300;

-- 場所も同じ
INSERT INTO reach.spot_walk (spot_id, node_id, access_m)
SELECT s.spot_id, n.node_id, n.d
FROM reach.spot s
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, s.geom::geography) AS d
  FROM reach.walk_node w
  ORDER BY w.geom <-> ST_Transform(s.geom, 6668)
  LIMIT 1
) n
WHERE n.d <= 300;

ANALYZE reach.station_walk;
ANALYZE reach.spot_walk;
