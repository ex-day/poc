# Cognito × Spring Security（標準の OIDC でつなげるか）

Issue：[#38](https://github.com/ex-day/poc/issues/38)。認証サービスの選定（[platform#75](https://github.com/ex-day/platform/issues/75)）のうち、「D：標準の OIDC のライブラリでそのままつなげるか」を確かめる最小のアプリ。Cognito の試しの手順と、それまでの結果は platform の `docs/infra/cognito-trial.md`（[platform#130](https://github.com/ex-day/platform/pull/130)）にある。

## 作り

| 確かめること | 作り | 標準のままか |
|---|---|---|
| ログイン → コールバック → ユーザー情報 | Spring Security の OAuth2 Client（`oauth2Login`）。`issuer-uri` の discovery で入口を読む。認可コード＋PKCE | 標準 |
| トークンをブラウザに渡さない（platform#75 の要件1） | トークンはサーバー側のセッション（Spring Security の既定）。ブラウザには JSESSIONID（HttpOnly・SameSite=Lax）だけ。CSRF は既定（有効） | 標準 |
| `lang=ja`、「Google で続ける」（`identity_provider`）、`login_hint` | 標準の解決器（`DefaultOAuth2AuthorizationRequestResolver`）に、追加の値を足すだけ（`SecurityConfig`） | 標準の上に足す |
| パスワードなしの登録（cognito-trial.md の案1） | `/signup` でメールアドレスを受け、サーバーから Cognito の `SignUp` をパスワードなしで呼ぶ（`SignUpClient`）→ 標準のログインの入口へ | **Cognito 専用**（トークンは扱わない） |
| パスキーの登録 | `/passkeys/add` から Cognito のパスキーの登録のページへ送る | Cognito 専用（リダイレクトだけ） |
| ログアウト | アプリのセッションを消し、Cognito の `/logout` へ送る | **Cognito 専用**（Cognito は OIDC の標準のログアウトの入口を公開していないとみられる。確かめる） |

## Cognito 側の準備

試しのユーザープール（platform の `cognito-trial.md` で作ったもの）の App Client に、次を設定する。

- 許可されているコールバック URL：`http://localhost:3000/login/oauth2/code/cognito` と `http://localhost:3000/`（後者はパスキーの登録のあとに戻る先）
- 許可されているサインアウト URL：`http://localhost:3000/`
- OAuth のスコープ：`openid`、`email`
- 認証フロー：`ALLOW_USER_AUTH`（選択ベースのサインイン）

## 動かす

Java 25 が要る（platform の apps/api と同じ）。

```bash
cd auth/cognito-spring
cp local.env.example local.env   # 値を入れる。local.env は git の管理外
set -a; source local.env; set +a
./gradlew bootRun
```

<http://localhost:3000> を開く。

## 確かめること（結果は下に書く）

| # | やること | 見ること |
|---|---|---|
| 1 | 起動する | `issuer-uri` の discovery が通って起動するか |
| 2 | 「メール・パスキーでログイン」 | managed login が日本語で出て、パスキーかメールのコードで入れ、クレームの表が出るか |
| 3 | ブラウザの開発者ツールで cookie を見る | localhost の cookie が JSESSIONID（HttpOnly）だけか。トークンが見えないか |
| 4 | ログアウト → 「Google で続ける」 | Cognito の画面を出さずに Google へ移り、戻ってこられるか。クレームに `identities` があるか |
| 5 | ログアウト → 「メールアドレスで登録する」 | パスワードなしで登録 → ログインの画面にメールアドレスが入っているか（`login_hint`）→ コードで入れるか |
| 6 | 5 のあと「パスキーを追加する」 | パスキーの登録のページが出て、登録後に戻ってこられるか |
| 7 | ログアウト | アプリのセッションが消え、Cognito の側でもログアウトされるか（もう一度ログインの画面を開くと、入力を求められるか） |

## 結果

（試したら書く）
