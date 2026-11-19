package main

import (
	"fmt"
	"os"
)

type Box struct{ v string }

func (b *Box) Self() *Box { return b }

func main() {
	var t Box
	p := t.Self()
	t = Box{v: os.Getenv("API_KEY")}
	fmt.Println(len(t.v))
	os.WriteFile("/tmp/out.txt", []byte(p.v), 0o600)
}
