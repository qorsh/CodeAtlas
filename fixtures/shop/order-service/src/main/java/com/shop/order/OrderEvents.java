package com.shop.order;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class OrderEvents {
    private final KafkaTemplate<String, OrderCreated> kafkaTemplate;

    public OrderEvents(KafkaTemplate<String, OrderCreated> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishCreated(Order order) {
        kafkaTemplate.send("order.created", new OrderCreated(order.getId(), order.getAmount()));
    }
}
