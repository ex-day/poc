package io.github.exday.poc.cognito;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 標準の OIDC の設定（spring.security.oauth2.client.*）の外で使う Cognito の値。
 * ログアウト・パスキーの登録・パスワードなしの登録（SignUp）にだけ使う。
 */
@ConfigurationProperties(prefix = "exday.cognito")
public record CognitoProperties(
        String region,
        String domain,
        String clientId,
        String clientSecret,
        String appBaseUrl) {
}
