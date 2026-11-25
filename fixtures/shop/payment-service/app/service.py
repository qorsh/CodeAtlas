from .db import session
from .models import Payment


class PaymentService:
    """결제 승인과 조회."""

    def charge(self, order_id, amount):
        payment = Payment(order_id=order_id, amount=amount, status="APPROVED")
        session.add(payment)
        session.commit()
        return {"status": payment.status}

    def find(self, payment_id):
        return session.query(Payment).filter_by(id=payment_id).first()

    def mark_pending(self, order_id):
        payment = session.query(Payment).filter_by(order_id=order_id).first()
        if payment is None:
            session.add(Payment(order_id=order_id, amount=0, status="PENDING"))
            session.commit()
