package main

import "net/http"

func listUsers(w http.ResponseWriter, r *http.Request) {}

func main() {
	http.HandleFunc("/users", listUsers)
	http.ListenAndServe(":8080", nil)
}
