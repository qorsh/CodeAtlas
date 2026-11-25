import axios from 'axios';

export type Order = { id: number; amount: number };

export async function fetchOrder(id: number): Promise<Order> {
  const response = await axios.get(`/api/orders/${id}`);
  return response.data;
}

export async function createOrder(customerId: number, amount: number): Promise<Order> {
  const response = await axios.post('/api/orders', { customerId, amount });
  return response.data;
}

export async function cancelOrder(id: number): Promise<void> {
  await fetch(`/api/orders/${id}/cancel`, { method: 'POST' });
}

export async function fetchStock(sku: string): Promise<number> {
  const response = await fetch(`/inventory/${sku}`);
  return (await response.json()).quantity;
}
