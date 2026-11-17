package main

import (
	"fmt"
	"os"
)

func leak() {
	s := []string{""}
	t := s
	t = []string{os.Getenv("API_KEY")}
	fmt.Println(len(t))
	os.WriteFile("o.txt", []byte(s[0]), 0o600)
}
