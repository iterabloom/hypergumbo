package main

import (
	"fmt"
	"os"
)

type T struct{ F string }

func leak() {
	a := &T{}
	b := a
	b.F = os.Getenv("API_KEY")
	fmt.Println(b.F)
	os.WriteFile("o.txt", []byte(a.F), 0o600)
}
