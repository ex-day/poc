package io.github.exday.poc.cognito;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class CognitoSpringApplication {

    public static void main(String[] args) {
        SpringApplication.run(CognitoSpringApplication.class, args);
    }
}
