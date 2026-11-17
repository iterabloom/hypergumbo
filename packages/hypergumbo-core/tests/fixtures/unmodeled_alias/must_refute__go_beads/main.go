package main

import (
	"fmt"
	"os"
	"runtime"
)

func collect(path string) map[string]string {
	return map[string]string{"os_arch": runtime.GOOS + "/" + path}
}

func run(path string) {
	fmt.Fprintf(os.Stderr, "no directory at %s\n", path)
	info := collect(path)
	fmt.Printf("Platform: %s\n", info["os_arch"])
	fmt.Fprintf(os.Stderr, "no database at %s\n", path)
}
