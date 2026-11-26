import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../api/booking_api.dart';
import '../models/booking.dart';

class BookingListPage extends StatefulWidget {
  const BookingListPage({super.key});

  @override
  State<BookingListPage> createState() => _BookingListPageState();
}

class _BookingListPageState extends State<BookingListPage> {
  final _api = BookingApi(Dio());
  List<Booking> _items = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final items = await _api.fetchBookings();
    setState(() {
      _items = items;
    });
  }

  String get title => '예약 ${_items.length}건';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: ListView(children: [for (final b in _items) ListTile(title: Text('객실 ${b.roomId}'), onTap: () => _api.cancel(b.id))]),
    );
  }
}
