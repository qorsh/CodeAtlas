package com.shop.order;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

/** 결제 서비스(Python)를 부르는 클라이언트. */
@Component
public class PaymentClient {
    private final RestTemplate restTemplate = new RestTemplate();

    public PaymentResult requestPayment(Order order) {
        PaymentRequest body = new PaymentRequest(order.getId(), order.getAmount());
        return restTemplate.postForObject("http://payment-service/payments", body, PaymentResult.class);
    }
}
