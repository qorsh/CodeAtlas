package com.shop.order;

import jakarta.persistence.*;

@Entity
@Table(name = "orders")
public class Order {
    @Id @GeneratedValue
    private Long id;
    private Long customerId;
    private long amount;
    private String status = "PLACED";

    public static Order from(CreateOrderRequest request) {
        Order order = new Order();
        order.customerId = request.customerId();
        order.amount = request.amount();
        return order;
    }

    public void cancel() {
        this.status = "CANCELLED";
    }

    public Long getId() { return id; }
    public long getAmount() { return amount; }
}
