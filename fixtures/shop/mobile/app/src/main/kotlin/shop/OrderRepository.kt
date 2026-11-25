package shop

class OrderRepository(private val api: OrderApi) {
    suspend fun load(id: Long): OrderDto = api.order(id)

    suspend fun place(customerId: Long, amount: Long): OrderDto =
        api.create(mapOf("customerId" to customerId, "amount" to amount))
}
