// 관리자 화면은 주문 한 건만 조회한다. 이 경로는 쇼핑 주문 서버와 예약 서버에 모두 있어, 코드만 봐서는 어느 서버인지 알 수 없다.
export async function fetchOrder(id: number): Promise<unknown> {
  return (await fetch(`/api/orders/${id}`)).json();
}
