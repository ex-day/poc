package io.github.exday.poc.cognito;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.client.web.DefaultOAuth2AuthorizationRequestResolver;
import org.springframework.security.oauth2.client.web.OAuth2AuthorizationRequestCustomizers;
import org.springframework.security.oauth2.client.web.OAuth2AuthorizationRequestResolver;
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationRequest;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.logout.LogoutSuccessHandler;

@Configuration
public class SecurityConfig {

    /** ex-day のボタンから直接送ってよい外部のログイン（Cognito の IdP 名） */
    private static final Pattern ALLOWED_IDP = Pattern.compile("Google|SignInWithApple");
    private static final Pattern EMAIL = Pattern.compile("^[^\\s@<>\"]{1,64}@[^\\s@<>\"]{1,255}$");

    @Bean
    SecurityFilterChain securityFilterChain(HttpSecurity http,
            ClientRegistrationRepository registrations,
            CognitoProperties cognito) throws Exception {
        http
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/", "/signup", "/error").permitAll()
                        .anyRequest().authenticated())
                // 標準の OIDC のログイン（認可コード＋PKCE）。トークンはサーバー側のセッションに置く
                .oauth2Login(login -> login
                        .authorizationEndpoint(ep -> ep.authorizationRequestResolver(
                                authorizationRequestResolver(registrations))))
                // CSRF は Spring Security の既定（有効）のまま。フォームには隠しの印を入れる
                .logout(logout -> logout
                        .logoutUrl("/logout")
                        .logoutSuccessHandler(cognitoLogout(cognito)));
        return http.build();
    }

    /**
     * 標準の解決器に、Cognito の追加の値だけを足す。
     * - lang=ja：managed login を日本語にする
     * - identity_provider：ex-day の「Google で続ける」から、Cognito の画面を飛ばして直接送る
     * - login_hint：ex-day の登録の画面から来たとき、メールアドレスを入れておく（効くかを確かめる）
     */
    private OAuth2AuthorizationRequestResolver authorizationRequestResolver(ClientRegistrationRepository registrations) {
        var delegate = new DefaultOAuth2AuthorizationRequestResolver(registrations, "/oauth2/authorization");
        delegate.setAuthorizationRequestCustomizer(OAuth2AuthorizationRequestCustomizers.withPkce());
        return new OAuth2AuthorizationRequestResolver() {
            @Override
            public OAuth2AuthorizationRequest resolve(HttpServletRequest request) {
                return customize(delegate.resolve(request), request);
            }

            @Override
            public OAuth2AuthorizationRequest resolve(HttpServletRequest request, String clientRegistrationId) {
                return customize(delegate.resolve(request, clientRegistrationId), request);
            }
        };
    }

    private static OAuth2AuthorizationRequest customize(OAuth2AuthorizationRequest original, HttpServletRequest request) {
        if (original == null) {
            return null;
        }
        String idp = request.getParameter("idp");
        String hint = request.getParameter("login_hint");
        return OAuth2AuthorizationRequest.from(original)
                .additionalParameters(params -> {
                    params.put("lang", "ja");
                    if (idp != null && ALLOWED_IDP.matcher(idp).matches()) {
                        params.put("identity_provider", idp);
                    }
                    if (hint != null && EMAIL.matcher(hint).matches()) {
                        params.put("login_hint", hint);
                    }
                })
                .build();
    }

    /**
     * Cognito は OIDC の標準のログアウトの入口（end_session_endpoint）を公開していないとみられるので、
     * 標準の OidcClientInitiatedLogoutSuccessHandler は使えない。Cognito の /logout へ送る（ここだけ Cognito 専用）。
     * logout_uri は App Client の「許可されているサインアウト URL」に登録しておく。
     */
    private static LogoutSuccessHandler cognitoLogout(CognitoProperties cognito) {
        return (request, response, authentication) -> {
            String url = cognito.domain() + "/logout"
                    + "?client_id=" + enc(cognito.clientId())
                    + "&logout_uri=" + enc(cognito.appBaseUrl() + "/");
            response.sendRedirect(url);
        };
    }

    static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }
}
