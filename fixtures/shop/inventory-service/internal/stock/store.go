package stock

import (
	"database/sql"
	"errors"
)

// Item 은 품목 하나의 재고다.
type Item struct {
	SKU      string `json:"sku"`
	Quantity int    `json:"quantity"`
}

// Store 는 재고 저장소가 해야 할 일.
type Store interface {
	Find(sku string) (Item, error)
	Reserve(sku string, quantity int) error
}

type SQLStore struct {
	db *sql.DB
}

var ErrInsufficient = errors.New("재고 부족")

func NewSQLStore(db *sql.DB) *SQLStore {
	return &SQLStore{db: db}
}

func (s *SQLStore) Find(sku string) (Item, error) {
	item := Item{SKU: sku}
	err := s.db.QueryRow(`SELECT quantity FROM stock WHERE sku = $1`, sku).Scan(&item.Quantity)
	return item, err
}

func (s *SQLStore) Reserve(sku string, quantity int) error {
	res, err := s.db.Exec(`UPDATE stock SET quantity = quantity - $2 WHERE sku = $1 AND quantity >= $2`, sku, quantity)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return ErrInsufficient
	}
	return nil
}
