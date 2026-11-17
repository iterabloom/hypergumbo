package main

import (
	"fmt"
	"os"
)

var G []string

func leak() {
	s := []string{""}
	G = s
	s[0] = os.Getenv("API_KEY")
	fmt.Println(len(s))
	os.WriteFile("o.txt", []byte(s[0]), 0o600)
}
