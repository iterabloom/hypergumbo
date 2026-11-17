package main

import (
	"fmt"
	"os"
)

func leak() {
	inner := []string{""}
	outer := [][]string{inner}
	inner[0] = os.Getenv("API_KEY")
	fmt.Println(len(inner))
	os.WriteFile("o.txt", []byte(outer[0][0]), 0o600)
}
