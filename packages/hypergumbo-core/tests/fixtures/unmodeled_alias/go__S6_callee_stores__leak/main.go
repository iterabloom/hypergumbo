package main

import (
	"fmt"
	"os"
)

func keep(r map[string][]string, v []string) { r["k"] = v }

func leak() {
	reg := map[string][]string{}
	s := []string{""}
	keep(reg, s)
	s[0] = os.Getenv("API_KEY")
	fmt.Println(len(s))
	os.WriteFile("o.txt", []byte(reg["k"][0]), 0o600)
}
