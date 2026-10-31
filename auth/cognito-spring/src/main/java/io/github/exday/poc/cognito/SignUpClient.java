package io.github.exday.poc.cognito;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

/**
 * Cognito の SignUp をパスワードなしで呼ぶ（platform の docs/infra/cognito-trial.md の案1）。
 * managed login の登録の画面はパスワードを求めるため、登録だけ ex-day の画面で受ける。
 * SignUp は AWS のアクセスキーなしで呼べる（ClientId と SecretHash だけ）。トークンは扱わない。
 * 本番では、ボットの大量登録への手当て（CAPTCHA・量の制限など）が要る。
 */
@Component
public class SignUpClient {

    private final CognitoProperties cognito;
    private final RestClient rest = RestClient.create();

    public SignUpClient(CognitoProperties cognito) {
        this.cognito = cognito;
    }

    /** 成功なら null、失敗なら Cognito の応答（例：UsernameExistsException）を返す */
    public String signUpWithoutPassword(String email) {
        String body = "{"
                + "\"ClientId\":" + json(cognito.clientId()) + ","
                + "\"SecretHash\":" + json(secretHash(email)) + ","
                + "\"Username\":" + json(email) + ","
                + "\"UserAttributes\":[{\"Name\":\"email\",\"Value\":" + json(email) + "}]"
                + "}";
        try {
            rest.post()
                    .uri("https://cognito-idp." + cognito.region() + ".amazonaws.com/")
                    .contentType(MediaType.parseMediaType("application/x-amz-json-1.1"))
                    .header("X-Amz-Target", "AWSCognitoIdentityProviderService.SignUp")
                    .body(body)
                    .retrieve()
                    .toBodilessEntity();
            return null;
        } catch (RestClientResponseException e) {
            return e.getStatusCode() + " " + e.getResponseBodyAsString();
        }
    }

    /** SecretHash = Base64(HMAC-SHA256(クライアントシークレット, ユーザー名 + クライアント ID)) */
    private String secretHash(String username) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(cognito.clientSecret().getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] raw = mac.doFinal((username + cognito.clientId()).getBytes(StandardCharsets.UTF_8));
            return Base64.getEncoder().encodeToString(raw);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** JSON の文字列にする（呼ぶ側でメールアドレスの形は確かめている） */
    private static String json(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
