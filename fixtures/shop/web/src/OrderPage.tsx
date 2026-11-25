import { useEffect, useState } from 'react';
import { cancelOrder, fetchOrder, Order } from './api';

export function OrderPage({ id }: { id: number }) {
  const [order, setOrder] = useState<Order | null>(null);
  useEffect(() => {
    fetchOrder(id).then(setOrder);
  }, [id]);
  if (!order) return <p>주문을 불러오는 중입니다</p>;
  return (
    <section>
      <h1>주문 {order.id}</h1>
      <button onClick={() => cancelOrder(order.id)}>주문 취소</button>
    </section>
  );
}
