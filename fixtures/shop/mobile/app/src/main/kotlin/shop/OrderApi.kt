package shop

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

data class OrderDto(val id: Long, val amount: Long)

/** 주문 서비스 API (Retrofit). */
interface OrderApi {
    @GET("api/orders/{id}")
    suspend fun order(@Path("id") id: Long): OrderDto

    @POST("api/orders")
    suspend fun create(@Body body: Map<String, Long>): OrderDto
}
