"""예약 API. 예약 웹과 예약 관리자 화면이 호출한다."""
from fastapi import APIRouter, HTTPException

from .erp_client import ErpClient
from .payment_client import PaymentClient

router = APIRouter(prefix="/api")
erp = ErpClient()
payments = PaymentClient()


@router.get("/bookings")
def list_bookings():
    return erp.rooms()


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: int):
    room = erp.room(booking_id)
    if room is None:
        raise HTTPException(status_code=404, detail="예약을 찾을 수 없습니다")
    return room


@router.post("/bookings")
def create_booking(body: dict):
    """객실을 레거시 ERP 에 잡아 두고 결제를 요청한다."""
    erp.reserve(body["roomId"])
    return payments.charge(body["bookingId"], body["amount"])


@router.delete("/bookings/{booking_id}")
def cancel_booking(booking_id: int):
    return {"cancelled": booking_id}


@router.get("/orders/{order_id}")
def get_order(order_id: int):
    """예약에 딸린 주문 한 건. 쇼핑 제품의 주문 API 와 경로가 같다."""
    return {"id": order_id}
