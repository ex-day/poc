plugins {
    java
    id("org.springframework.boot") version "4.1.1"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "io.github.exday.poc"
version = "0.0.1-SNAPSHOT"

// platform の apps/api と同じく Java 25
java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    // Spring Security の OAuth2 Client（OIDC のログイン）。トークンはサーバー側のセッションに持つ
    implementation("org.springframework.boot:spring-boot-starter-security-oauth2-client")
}
