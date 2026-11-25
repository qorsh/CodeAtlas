package api

import (
	"encoding/json"
	"net/http"

	"shop/inventory/internal/stock"
)

// Server 는 재고 API 의 진입점이다.
type Server struct {
	store    stock.Store
	payments *PaymentClient
}

type reserveRequest struct {
	SKU       string `json:"sku"`
	Quantity  int    `json:"quantity"`
	PaymentID int64  `json:"paymentId"`
}

func NewServer(store stock.Store, payments *PaymentClient) *Server {
	return &Server{store: store, payments: payments}
}

// Reserve 는 결제가 승인된 주문에 한해 재고를 잡는다.
func (s *Server) Reserve(w http.ResponseWriter, r *http.Request) {
	var body reserveRequest
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "요청 형식이 잘못되었습니다", http.StatusBadRequest)
		return
	}
	if !s.payments.Approved(body.PaymentID) {
		http.Error(w, "결제가 승인되지 않았습니다", http.StatusConflict)
		return
	}
	if err := s.store.Reserve(body.SKU, body.Quantity); err != nil {
		http.Error(w, "재고가 부족합니다", http.StatusConflict)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
