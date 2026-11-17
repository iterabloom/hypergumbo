package main

import (
	"fmt"
	"os"
)

func readKey() (string, error) {
	return os.Getenv("API_KEY"), nil
}

func run() {
	_, err := readKey()
	fmt.Println(err)
	os.WriteFile("o.txt", []byte("x"), 0o600)
}
