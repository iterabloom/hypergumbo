package main

import (
	"fmt"
	"os"
)

type Box struct{ v string }

func (b *Box) Get() string { return b.v }

func main() {
	var t Box
	t = Box{v: os.Getenv("API_KEY")}
	fmt.Println(len(t.v))
	os.WriteFile("/tmp/out.txt", []byte(t.Get()), 0o600)
}
