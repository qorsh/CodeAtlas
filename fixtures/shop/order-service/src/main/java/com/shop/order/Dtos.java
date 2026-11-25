package com.shop.order;

record CreateOrderRequest(Long customerId, long amount) {}

record OrderResponse(Long id, long amount) {
    static OrderResponse from(Order order) {
        return new OrderResponse(order.getId(), order.getAmount());
    }
}

record PaymentRequest(Long orderId, long amount) {}

record PaymentResult(String status) {}

record OrderCreated(Long orderId, long amount) {}

class OrderNotFoundException extends RuntimeException {
    OrderNotFoundException(String message) {
        super(message);
    }
}
