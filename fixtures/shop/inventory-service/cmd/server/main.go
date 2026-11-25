// 재고 서비스. 주문 서비스(Java)와 웹이 호출한다.
package main

import (
	"database/sql"
	"log"
	"net/http"

	"shop/inventory/internal/api"
	"shop/inventory/internal/stock"
)

func main() {
	db, err := sql.Open("postgres", "postgres://localhost/inventory")
	if err != nil {
		log.Fatal(err)
	}
	server := api.NewServer(stock.NewSQLStore(db), api.NewPaymentClient("http://payment-service"))
	log.Fatal(http.ListenAndServe(":8080", routes(server)))
}

// routes 는 이 서비스가 제공하는 API 를 한곳에 모은다.
func routes(server *api.Server) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /inventory/reserve", server.Reserve)
	mux.HandleFunc("GET /inventory/{sku}", server.Get)
	return mux
}
