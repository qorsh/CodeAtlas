/// 서버 주소. 개발 서버와 운영 서버는 코드가 같고 주소만 다르다.
class ApiConfig {
  static const baseUrl = 'https://api.booking.example';
  static const timeout = Duration(seconds: 10);
}
