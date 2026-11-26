import 'package:flutter/material.dart';

import 'pages/booking_list_page.dart';

void main() => runApp(const BookingApp());

/// 앱의 뿌리. 예약 목록 화면으로 시작한다.
class BookingApp extends StatelessWidget {
  const BookingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(title: '예약', home: const BookingListPage());
  }
}
