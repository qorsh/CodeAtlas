class Booking {
  const Booking({required this.id, required this.roomId});

  final int id;
  final int roomId;

  factory Booking.fromJson(Map<String, dynamic> json) => Booking(id: json['id'] as int, roomId: json['roomId'] as int);

  Map<String, dynamic> toJson() => {'id': id, 'roomId': roomId};
}
