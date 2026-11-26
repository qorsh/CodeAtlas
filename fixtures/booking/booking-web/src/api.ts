export type Booking = { id: number; roomId: number };

export async function fetchBookings(): Promise<Booking[]> {
  return (await fetch('/api/bookings')).json();
}

export async function fetchBooking(id: number): Promise<Booking> {
  return (await fetch(`/api/bookings/${id}`)).json();
}

export async function createBooking(roomId: number, amount: number): Promise<Booking> {
  const response = await fetch('/api/bookings', { method: 'POST', body: JSON.stringify({ roomId, amount }) });
  return response.json();
}

// 쇼핑 제품의 주문 API 와 경로가 같다. 예약 웹은 예약 서버를 부른다.
export async function fetchBookingOrder(id: number): Promise<unknown> {
  return (await fetch(`/api/orders/${id}`)).json();
}

// 공지는 레거시 ERP 에서 바로 읽는다.
export async function fetchNotices(): Promise<unknown> {
  return (await fetch('https://erp.legacy.example/erp/notices')).json();
}
