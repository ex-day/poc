# ex-day PoC

[ex-day](https://github.com/ex-day/platform) の技術検証（PoC）のコードと結果をまとめたリポジトリです。

ex-day は、今いる場所、使える時間、興味をもとに、地域の歴史や出来事などの「発見（Discovery）」と出会うためのサービスです。サービス本体の設計・開発は [ex-day/platform](https://github.com/ex-day/platform) で進めています。

## PoC の一覧

| フォルダ | 内容 | 検討の issue |
|---|---|---|
| [`vector/`](vector/) | pgvector と手元で動く埋め込みモデルで、関連する Discovery を探す。完全一致とベクトル検索、モデル（e5・Ruri v3）、会話の話題単位のベクトルを比べる | [#1](https://github.com/ex-day/poc/issues/1) |
| [`vector/scale/`](vector/scale/) | 件数を 1千〜100万件に増やしたときのベクトル検索の速さとインデックスの振る舞い（全件比較と HNSW、再現率、絞り込みと iterative scan、部分インデックス） | [#9](https://github.com/ex-day/poc/issues/9) |
| [`context/`](context/) | 会話の投稿どうしや、返信で積み重なった文脈との近さを埋め込みで比べ、新しい投稿がどの話題の続きかを判定できるかを見る | [#2](https://github.com/ex-day/poc/issues/2) |

今後、地名の辞書と位置の判定（[platform#82](https://github.com/ex-day/platform/issues/82)）の PoC もここに置く予定です。

## 使い方

検証用の DB（PostgreSQL 18 ＋ pgvector ＋ PostGIS）は Docker で動かします。

```bash
docker compose up -d
```

`docker/postgres/init/` の SQL は、DB が空の初回起動のときだけ自動で実行されます。各 PoC の手順は、それぞれのフォルダの README を見てください。

## 判断の記録

このリポジトリには、検証のコードと結果を置きます。検証を受けて何をどう判断したかは、[ex-day/platform の issue](https://github.com/ex-day/platform/issues) に記録しています。

## 公開の方針

このリポジトリは、ex-day の検証の記録として公開を続けます。将来 ex-day の運営やサービスを誰かに引き継ぐことになった場合も、このリポジトリは公開のまま残すことを条件にします。

## License

[MIT License](LICENSE)
