//go:build linux

// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"errors"
	"os"
	"syscall"
)

func owned(info os.FileInfo, regular bool) error {
	data, ok := info.Sys().(*syscall.Stat_t)
	if !ok || data.Uid != uint32(os.Geteuid()) ||
		(regular && data.Nlink != 1) {
		return errors.New("limiter path must have its own service-owned inode")
	}
	return nil
}

func trustedParent(info os.FileInfo) bool {
	data, ok := info.Sys().(*syscall.Stat_t)
	return ok && (data.Uid == 0 || data.Uid == uint32(os.Geteuid()))
}

func openPolicy(path string) (*os.File, error) {
	return os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}

func staleSocket(err error) bool {
	return errors.Is(err, syscall.ECONNREFUSED) || os.IsNotExist(err)
}
