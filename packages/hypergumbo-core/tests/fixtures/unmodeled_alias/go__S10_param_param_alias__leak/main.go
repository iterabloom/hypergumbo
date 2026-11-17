package main

import (
	"fmt"
	"os"
)

func leak(a, b []string) {
	a[0] = os.Getenv("API_KEY")
	fmt.Println(len(a))
	os.WriteFile("o.txt", []byte(b[0]), 0o600)
}

func main() {
	x := []string{""}
	leak(x, x)
}
