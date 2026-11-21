-- reach/osmwalk/build_osm.sql
-- OSM の歩きのグラフ（load_osm_walk.py が入れたもの）に索引を付け、駅グループ・場所を最寄りの点に結び付ける（Issue #46）。
-- 結び付け方は 07_build_walk.sql（N13）と同じ（駅グループはホームの重心、300m まで）。何度流しても同じ結果になる。

TRUNCATE reach.spot_walk, reach.station_walk;

-- 小さな島を除く：OSM の道には、ほかの道とつながっていない短い道の集まり（駅の中の通路・ホーム、敷地の中の道など）がある。
-- いちばん近い点に結ぶと、駅や場所がその島に結ばれ、どこへも歩けなくなる（1回目の比べで、大宮・川口などの駅がそうなった）。
-- つながっている点が 100 未満の集まりは、点と辺を消す（2026-11-19 の関東・メッシュ 5339 で、6,340 の島・約1.9万点）
DROP TABLE IF EXISTS reach.walk_component;
CREATE TABLE reach.walk_component AS
SELECT node AS node_id, component
FROM pgr_connectedComponents('SELECT id, source, target, cost, reverse_cost FROM reach.walk_edge');
CREATE TEMP TABLE small_island AS
SELECT c.node_id FROM reach.walk_component c
JOIN (SELECT component FROM reach.walk_component GROUP BY component HAVING count(*) < 100) s USING (component);
DELETE FROM reach.walk_edge e WHERE e.source IN (SELECT node_id FROM small_island);
DELETE FROM reach.walk_node n WHERE n.node_id IN (SELECT node_id FROM small_island);

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
