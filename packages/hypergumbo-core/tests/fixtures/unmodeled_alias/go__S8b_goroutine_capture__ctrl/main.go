package main

import (
	"fmt"
	"os"
)

func leak() {
	s := []string{""}
	t := s
	t[0] = os.Getenv("API_KEY")
	fmt.Println(len(t))
	go func() { os.WriteFile("o.txt", []byte(t[0]), 0o600) }()
}
