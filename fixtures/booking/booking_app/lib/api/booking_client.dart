import 'package:dio/dio.dart';
import 'package:retrofit/retrofit.dart';

import '../models/booking.dart';

part 'booking_client.g.dart';

/// Retrofit 방식으로 선언한 호출. 본문은 생성기가 만든다.
@RestApi()
abstract class BookingClient {
  factory BookingClient(Dio dio) = _BookingClient;

  @POST('/api/bookings')
  Future<Booking> create(@Body() Map<String, dynamic> body);
}
