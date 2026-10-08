FROM pgvector/pgvector:pg18

## PostGISを追加（pgvectorは入っている）
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-18-postgis-3 postgresql-18-postgis-3-scripts \
 && apt-get install -y --no-install-recommends postgresql-18-pgrouting \
 && rm -rf /var/lib/apt/lists/*
