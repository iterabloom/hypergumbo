package main

import (
	"fmt"
	"os"
)

func leak() {
	inner := []string{""}
	reg := map[string][]string{}
	reg["k"] = inner
	inner[0] = os.Getenv("API_KEY")
	fmt.Println(len(inner))
	os.WriteFile("o.txt", []byte(inner[0]), 0o600)
}
