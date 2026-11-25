package com.shop.order;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

/** 재고 서비스(Go)를 부르는 클라이언트. */
@Component
public class InventoryClient {
    private final RestTemplate restTemplate = new RestTemplate();

    public void reserve(String sku, int quantity, long paymentId) {
        restTemplate.postForObject("http://inventory-service/inventory/reserve", new ReserveRequest(sku, quantity, paymentId), Void.class);
    }

    record ReserveRequest(String sku, int quantity, long paymentId) {}
}
