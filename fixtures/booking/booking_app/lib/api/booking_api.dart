import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:http/http.dart' as http;

import '../config.dart';
import '../models/booking.dart';

/// 예약 서버 호출. dio 로 목록을, http 패키지로 한 건을 읽는다.
class BookingApi {
  BookingApi(this._dio);

  final Dio _dio;

  Future<List<Booking>> fetchBookings() async {
    final response = await _dio.get('/api/bookings');
    return (response.data as List).map((e) => Booking.fromJson(e)).toList();
  }

  Future<Booking> fetchBooking(int id) async {
    final response = await http.get(Uri.parse('${ApiConfig.baseUrl}/api/bookings/$id'));
    return Booking.fromJson(jsonDecode(response.body));
  }

  Future<void> cancel(int id) => _dio.delete('/api/bookings/$id');
}
