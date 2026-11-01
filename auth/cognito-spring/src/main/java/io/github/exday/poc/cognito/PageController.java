package io.github.exday.poc.cognito;

import java.net.URI;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Pattern;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.view.RedirectView;
import org.springframework.web.util.HtmlUtils;

/** 確かめるための最小の画面。見た目は気にしない */
@RestController
public class PageController {

    private static final Pattern EMAIL = Pattern.compile("^[^\\s@<>\"]{1,64}@[^\\s@<>\"]{1,255}$");

    private final CognitoProperties cognito;
    private final SignUpClient signUp;

    public PageController(CognitoProperties cognito, SignUpClient signUp) {
        this.cognito = cognito;
        this.signUp = signUp;
    }

    @GetMapping(value = "/", produces = "text/html;charset=UTF-8")
    public String home(@AuthenticationPrincipal OidcUser user, CsrfToken csrf) {
        var html = new StringBuilder(head("Cognito × Spring Security"));
        if (user == null) {
            html.append("<p>ログインしていません。</p>")
                    .append("<ul>")
                    .append("<li><a href=\"/oauth2/authorization/cognito?idp=Google\">Google で続ける</a>（Cognito の画面を飛ばす）</li>")
                    .append("<li><a href=\"/oauth2/authorization/cognito\">メール・パスキーでログイン</a>（managed login）</li>")
                    .append("<li><a href=\"/signup\">メールアドレスで登録する</a>（パスワードなし）</li>")
                    .append("</ul>");
        } else {
            html.append("<p>ログインしています。</p>")
                    .append("<p>トークンはサーバー側のセッションにあり、ブラウザには渡していません。")
                    .append("ブラウザの開発者ツールで cookie が JSESSIONID（HttpOnly）だけかを確かめてください。</p>")
                    .append("<h2>ID トークンのクレーム</h2><table border=\"1\" cellpadding=\"4\">");
            Map<String, Object> claims = new TreeMap<>(user.getClaims());
            claims.forEach((k, v) -> html.append("<tr><th>").append(esc(k)).append("</th><td>")
                    .append(esc(String.valueOf(v))).append("</td></tr>"));
            html.append("</table>")
                    .append("<ul>")
                    .append("<li><a href=\"/passkeys/add\">パスキーを追加する</a>")
                    .append("（Cognito の登録のページへ送る。ログインから1時間以内）</li>")
                    .append("</ul>")
                    .append("<form method=\"post\" action=\"/logout\">")
                    .append(csrfField(csrf))
                    .append("<button type=\"submit\">ログアウト</button></form>");
        }
        return html.append("</body></html>").toString();
    }

    @GetMapping(value = "/signup", produces = "text/html;charset=UTF-8")
    public String signupForm(CsrfToken csrf) {
        return head("登録（パスワードなし）")
                + "<form method=\"post\" action=\"/signup\">"
                + csrfField(csrf)
                + "<label>メールアドレス <input type=\"email\" name=\"email\" required></label> "
                + "<button type=\"submit\">登録する</button></form>"
                + "<p>登録のあと、ログインの画面（managed login）でメールに届くコードを入れてください。</p>"
                + "<p><a href=\"/\">戻る</a></p></body></html>";
    }

    @PostMapping(value = "/signup", produces = "text/html;charset=UTF-8")
    public ResponseEntity<String> signup(@RequestParam String email) {
        if (!EMAIL.matcher(email).matches()) {
            return ResponseEntity.badRequest().body(head("登録（パスワードなし）")
                    + "<p>メールアドレスの形が正しくありません。</p>"
                    + "<p><a href=\"/signup\">戻る</a></p></body></html>");
        }
        String error = signUp.signUpWithoutPassword(email);
        if (error != null) {
            return ResponseEntity.badRequest().body(head("登録（パスワードなし）")
                    + "<p>登録できませんでした：</p><pre>" + esc(error) + "</pre>"
                    + "<p><a href=\"/signup\">戻る</a></p></body></html>");
        }
        // 登録できたら、標準のログインの入口へ。メールアドレスを login_hint で渡す（効くかを確かめる）
        return ResponseEntity.status(HttpStatus.FOUND)
                .location(URI.create("/oauth2/authorization/cognito?login_hint=" + SecurityConfig.enc(email)))
                .build();
    }

    /**
     * Cognito のパスキーの登録のページへ送る。managed login のセッション（cookie、1時間）が要る。
     * redirect_uri は App Client の「許可されているコールバック URL」に登録しておく（http://localhost:3000/）。
     */
    @GetMapping("/passkeys/add")
    public RedirectView addPasskey() {
        return new RedirectView(cognito.domain() + "/passkeys/add"
                + "?client_id=" + SecurityConfig.enc(cognito.clientId())
                + "&redirect_uri=" + SecurityConfig.enc(cognito.appBaseUrl() + "/"));
    }

    private static String head(String title) {
        return "<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\"><title>" + esc(title)
                + "</title></head><body><h1>" + esc(title) + "</h1>";
    }

    private static String csrfField(CsrfToken csrf) {
        return "<input type=\"hidden\" name=\"" + esc(csrf.getParameterName()) + "\" value=\"" + esc(csrf.getToken()) + "\">";
    }

    private static String esc(String s) {
        return HtmlUtils.htmlEscape(s);
    }
}
