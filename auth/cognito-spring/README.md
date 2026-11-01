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

2026-11-01（人が Mac で動かした）

| # | 結果 | メモ |
|---|---|---|
| 1 | OK | 起動した。最初は `local.env` を書き換えたあとに読み込み直しておらず、見本の値のまま動いた（環境変数を読み込み直して起動し直す） |
| 2 | OK | |
| 3 | OK | localhost の cookie は JSESSIONID（HttpOnly・SameSite=Lax）だけ。トークンは見えない。もう1つの `Idea-…` は IntelliJ のもので関係ない |
| 4 | OK | Google でのログインは問題なし |
| 5 | 一部 | 登録 → Cognito の画面 → パスキーの登録まで進んだ。ただし、(a) ログインのあと `/.well-known/appspecific/com.chrome.devtools.json?continue` の 404 に飛んだ（Chrome の開発者ツールが自動で取りに来る URL を、ログイン後に戻る先として覚えていた。`/.well-known/**` を認証の対象から外し、ログイン後はトップへ戻すよう直した）、(b) **メールが2通届いた**（登録のときの確認コード「Your verification code」と、ログインのときのワンタイムパスワード「Your authentication code」） |
| 6 | OK | 登録してログインしたあと、Cognito の画面でパスキーの登録へ進めた |
| 7 | OK | アプリのセッションを消して Cognito の `/logout` へ送り、戻ってこられた |

### メールが2通になる件

- 1通目は `SignUp` のときに Cognito が送る確認コード。ユーザープールの「Cognito が自動でメッセージを送って検証・確認する」設定による
- 2通目はログインのときのワンタイムパスワード。こちらで入ると、メールアドレスは検証済みになり、ユーザーも確認済みになる（公式）
- 1通目を止める案：(1) 上の自動の検証・確認の設定を外す（managed login は、この設定を外すと登録の直後にエラーを出すことがあると公式にあるが、ex-day は登録を自前の画面でするので影響しない見込み）、(2) 登録の前の Lambda（Pre sign-up）でユーザーを確認済みにする。まず (1) を試す
- **2026-11-01：(1) で解決した。** ユーザーを消してから、自動の検証・確認の設定を外して登録し直すと、メールはログインのときのワンタイムパスワードの1通だけになり、登録 → ログイン → パスキーの登録まで通った

### まとめ（D）

- **標準の OIDC のライブラリ（Spring Security の OAuth2 Client）で、そのままつなげた。** ログイン、トークンをサーバー側に持つこと、Google へ直接送ることは標準の仕組み（＋追加の値）で足りた
- Cognito 専用に書いたのは、パスワードなしの登録（`SignUp`）、パスキーの登録のページへの転送、ログアウト（Cognito の `/logout`）の3か所。どれもトークンを扱わない
- Cognito の設定で要るもの：選択ベースのサインイン（パスキー＋メールのワンタイムパスワード）、App Client の `ALLOW_USER_AUTH`、自動の検証・確認を外す、コールバックとサインアウトの URL
