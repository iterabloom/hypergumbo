package main

import (
	"fmt"
	"os"
)

func leak() {
	m := map[string]string{}
	n := m
	n["k"] = os.Getenv("API_KEY")
	fmt.Println(len(n))
	os.WriteFile("o.txt", []byte(m["k"]), 0o600)
}
