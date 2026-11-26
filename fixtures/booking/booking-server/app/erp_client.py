"""레거시 ERP 호출. ERP 의 소스는 이 저장소에 없다."""
import requests

ERP_BASE = "https://erp.legacy.example"


class ErpClient:
    def rooms(self):
        return requests.get(f"{ERP_BASE}/erp/rooms").json()

    def room(self, room_id):
        return requests.get(f"{ERP_BASE}/erp/rooms/{room_id}").json()

    def reserve(self, room_id):
        return requests.post(f"{ERP_BASE}/erp/reservations", json={"roomId": room_id}).json()
