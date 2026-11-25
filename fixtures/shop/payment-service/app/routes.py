"""결제 API. 주문 서비스(Java)가 호출한다."""
from fastapi import APIRouter, HTTPException

from .service import PaymentService

router = APIRouter(prefix="/payments")
service = PaymentService()


@router.post("")
def create_payment(body: dict):
    """결제를 만들고 승인 결과를 돌려준다."""
    return service.charge(body["orderId"], body["amount"])


@router.get("/{payment_id}")
def get_payment(payment_id: int):
    payment = service.find(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="결제 내역이 없습니다")
    return payment
