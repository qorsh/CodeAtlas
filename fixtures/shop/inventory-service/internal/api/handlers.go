package api

import (
	"encoding/json"
	"net/http"
)

// Get 은 품목 하나의 남은 수량을 돌려준다. (Server 타입은 server.go 에 있다)
func (s *Server) Get(w http.ResponseWriter, r *http.Request) {
	item, err := s.store.Find(r.PathValue("sku"))
	if err != nil {
		http.Error(w, "품목을 찾을 수 없습니다", http.StatusNotFound)
		return
	}
	json.NewEncoder(w).Encode(item)
}
