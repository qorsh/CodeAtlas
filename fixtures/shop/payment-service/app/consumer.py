"""주문 생성 이벤트를 받아 결제 대기 상태를 만든다."""
import json

from kafka import KafkaConsumer

from .service import PaymentService


def run():
    service = PaymentService()
    consumer = KafkaConsumer("order.created", bootstrap_servers="kafka:9092")
    for message in consumer:
        event = json.loads(message.value)
        service.mark_pending(event["orderId"])
