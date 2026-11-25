package com.shop.order;

import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final OrderRepository repository;
    private final PaymentClient payments;
    private final OrderEvents events;

    public OrderService(OrderRepository repository, PaymentClient payments, OrderEvents events) {
        this.repository = repository;
        this.payments = payments;
        this.events = events;
    }

    public OrderResponse find(Long id) {
        Order order = repository.findById(id).orElseThrow(() -> new OrderNotFoundException("주문을 찾을 수 없습니다"));
        return OrderResponse.from(order);
    }

    /** 주문을 저장하고 결제를 요청한 뒤 이벤트를 발행한다. */
    public OrderResponse place(CreateOrderRequest request) {
        Order order = repository.save(Order.from(request));
        payments.requestPayment(order);
        events.publishCreated(order);
        return OrderResponse.from(order);
    }

    public void cancel(Long id) {
        Order order = repository.findById(id).orElseThrow(() -> new OrderNotFoundException("주문을 찾을 수 없습니다"));
        order.cancel();
        repository.save(order);
    }
}
