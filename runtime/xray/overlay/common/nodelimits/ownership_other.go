//go:build !linux

// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"errors"
	"os"
)

func owned(os.FileInfo, bool) error {
	return errors.New("native limiter control requires a Linux host")
}

func trustedParent(os.FileInfo) bool { return false }
func openPolicy(string) (*os.File, error) {
	return nil, errors.New("native limiter control requires Linux")
}
func staleSocket(error) bool { return false }
