"""결제 서비스 호출. 쇼핑 제품과 같은 결제 서버를 쓴다."""
import requests


class PaymentClient:
    def __init__(self, base_url="http://payment-service:8000"):
        self.base_url = base_url

    def charge(self, booking_id, amount):
        return requests.post(self.base_url + "/payments", json={"orderId": booking_id, "amount": amount}).json()
