package main

import (
	"fmt"
	"os"
)

func leak() {
	var buf string
	p := &buf
	buf = os.Getenv("API_KEY")
	fmt.Println(buf)
	os.WriteFile("o.txt", []byte(*p), 0o600)
}
