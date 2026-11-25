package com.shop.order;

import org.springframework.web.bind.annotation.*;

/** 주문 API. 웹과 모바일 앱이 함께 쓴다. */
@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService orders;

    public OrderController(OrderService orders) {
        this.orders = orders;
    }

    @GetMapping("/{id}")
    public OrderResponse find(@PathVariable Long id) {
        return orders.find(id);
    }

    @PostMapping
    public OrderResponse create(@RequestBody CreateOrderRequest request) {
        return orders.place(request);
    }

    @RequestMapping(value = "/{id}/cancel", method = RequestMethod.POST)
    public void cancel(@PathVariable Long id) {
        orders.cancel(id);
    }
}
