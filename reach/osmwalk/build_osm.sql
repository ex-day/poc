-- reach/osmwalk/build_osm.sql
-- OSM の歩きのグラフ（load_osm_walk.py が入れたもの）に索引を付け、駅グループ・場所を最寄りの点に結び付ける（Issue #46）。
-- 結び付け方は 07_build_walk.sql（N13）と同じ（駅グループはホームの重心、300m まで）。何度流しても同じ結果になる。

TRUNCATE reach.spot_walk, reach.station_walk;

-- 小さな島を除く：OSM の道には、ほかの道とつながっていない短い道の集まり（駅の中の通路・ホーム、敷地の中の道など）がある。
-- いちばん近い点に結ぶと、駅や場所がその島に結ばれ、どこへも歩けなくなる（1回目の比べで、大宮・川口などの駅がそうなった）。
-- つながっている点が 100 未満の集まりは、点と辺を消す（2026-11-19 の関東・メッシュ 5339 で、6,340 の島・約1.9万点）。
-- 100 はこのメッシュでの暫定の値。全国で一律に使うと、離島・山道・独立した公園などの正しい小さなネットワークも消しうるので、
-- 製品の実装では、消す島に結び付く駅・場所・到達点の数と例を見て決める（platform #163）。下で、その数を reach.island_snap に残す
DROP TABLE IF EXISTS reach.walk_component;
CREATE TABLE reach.walk_component AS
SELECT node AS node_id, component
FROM pgr_connectedComponents('SELECT id, source, target, cost, reverse_cost FROM reach.walk_edge');
CREATE TEMP TABLE small_island AS
SELECT c.node_id FROM reach.walk_component c
JOIN (SELECT component FROM reach.walk_component GROUP BY component HAVING count(*) < 100) s USING (component);
-- 消す前に、駅グループ・場所・到達点のうち、いちばん近い点（300m まで）が島の点になるものを残す
DROP TABLE IF EXISTS reach.island_snap;
CREATE TABLE reach.island_snap (kind text NOT NULL, id text NOT NULL, node_id bigint NOT NULL, island_nodes int NOT NULL);
CREATE INDEX IF NOT EXISTS walk_node_geom_idx ON reach.walk_node USING gist (geom);
WITH size AS (SELECT component, count(*)::int AS n FROM reach.walk_component GROUP BY component),
p AS (
  SELECT 'station_group' AS kind, group_code AS id, ST_Transform(ST_Centroid(ST_Collect(geom)), 6668) AS g
    FROM reach.station GROUP BY group_code
  UNION ALL SELECT 'spot', spot_id::text, ST_Transform(geom, 6668) FROM reach.spot
  UNION ALL SELECT 'access_point', ap_id::text, ST_Transform(geom, 6668) FROM reach.spot_access_point
)
INSERT INTO reach.island_snap (kind, id, node_id, island_nodes)
SELECT p.kind, p.id, n.node_id, s.n
FROM p
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, ST_Transform(p.g, 4326)::geography) AS d
  FROM reach.walk_node w ORDER BY w.geom <-> p.g LIMIT 1
) n
JOIN reach.walk_component c ON c.node_id = n.node_id
JOIN size s ON s.component = c.component
WHERE n.d <= 300 AND s.n < 100;

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
