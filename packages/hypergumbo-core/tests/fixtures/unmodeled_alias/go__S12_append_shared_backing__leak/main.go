package main

import (
	"fmt"
	"os"
)

func leak() {
	s := make([]string, 0, 4)
	u := s
	k := os.Getenv("API_KEY")
	s = append(s, k)
	fmt.Println(len(s))
	os.WriteFile("o.txt", []byte(u[:1][0]), 0o600)
}
