package main

import (
	"fmt"
	"os"
)

func view(v []string) []string { return v }

func leak() {
	s := []string{""}
	t := view(s)
	t[0] = os.Getenv("API_KEY")
	fmt.Println(len(t))
	os.WriteFile("o.txt", []byte(t[0]), 0o600)
}
