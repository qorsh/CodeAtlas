package api

import (
	"fmt"
	"net/http"
)

// PaymentClient 는 결제 서비스(Python)에 결제 상태를 묻는다.
type PaymentClient struct {
	baseURL string
	http    *http.Client
}

func NewPaymentClient(baseURL string) *PaymentClient {
	return &PaymentClient{baseURL: baseURL, http: http.DefaultClient}
}

func (c *PaymentClient) Approved(paymentID int64) bool {
	resp, err := c.http.Get(fmt.Sprintf("%s/payments/%d", c.baseURL, paymentID))
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}
