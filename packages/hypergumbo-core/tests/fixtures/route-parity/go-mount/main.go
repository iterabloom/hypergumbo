package main

import (
	"net/http"

	"github.com/go-chi/chi/v5"
)

func apiRouter() http.Handler { return nil }

func main() {
	r := chi.NewRouter()
	r.Mount("/api/v1", apiRouter())
	http.ListenAndServe(":8080", r)
}
