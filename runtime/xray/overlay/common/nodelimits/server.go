// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

type controlServer struct {
	http     *http.Server
	listener *net.UnixListener
	socket   os.FileInfo
}

type update struct {
	Policies []Policy `json:"policies"`
	Removals []string `json:"removals"`
}

func privateDirectory(path string) error {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path || len(path) > 90 {
		return errors.New("limiter directory requires a short, absolute clean path")
	}
	for current := filepath.Dir(path); ; current = filepath.Dir(current) {
		info, err := os.Lstat(current)
		if err != nil || !info.IsDir() || !trustedParent(info) || info.Mode()&os.ModeSymlink != 0 ||
			(info.Mode().Perm()&0022 != 0 && info.Mode()&os.ModeSticky == 0) {
			return errors.New("limiter directory has an unsafe parent")
		}
		if current == filepath.Dir(current) {
			break
		}
	}
	if err := os.Mkdir(path, 0700); err != nil && !os.IsExist(err) {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode().Perm() != 0700 {
		return errors.New("limiter directory must be private")
	}
	return owned(info, false)
}

func readPolicy(path string) ([]byte, error) {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || info.Size() > MaxBytes {
		return nil, errors.New("limiter policy must be a bounded private regular file")
	}
	if err := owned(info, true); err != nil {
		return nil, err
	}
	file, err := openPolicy(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	actual, err := file.Stat()
	if err != nil || !os.SameFile(info, actual) {
		return nil, errors.New("limiter policy changed")
	}
	raw, err := io.ReadAll(io.LimitReader(file, MaxBytes+1))
	if len(raw) > MaxBytes {
		return nil, errors.New("limiter policy is too large")
	}
	return raw, err
}

func persist(directory string, raw, expected []byte) error {
	path := filepath.Join(directory, "policy.json")
	current, err := readPolicy(path)
	if err != nil {
		return err
	}
	if !bytes.Equal(current, expected) {
		return errors.New("limiter policy was changed outside the runtime")
	}
	file, err := os.CreateTemp(directory, ".policy-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	defer file.Close()
	if _, err = file.Write(raw); err != nil {
		return err
	}
	if err = file.Sync(); err != nil {
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return err
	}
	parent, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer parent.Close()
	return parent.Sync()
}

func (m *Manager) Start() error {
	if m == nil {
		return nil
	}
	if m.server != nil {
		return nil
	}
	if err := privateDirectory(m.directory); err != nil {
		return err
	}
	raw, err := readPolicy(filepath.Join(m.directory, "policy.json"))
	if err != nil {
		return err
	}
	document := m.document
	if raw != nil {
		if err := decode(raw, &document); err != nil {
			return err
		}
		if err := document.validate(); err != nil {
			return err
		}
	}
	m.mu.Lock()
	m.install(document, raw)
	m.mu.Unlock()
	path := filepath.Join(m.directory, "control.sock")
	if info, err := os.Lstat(path); err == nil {
		if info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0600 {
			return errors.New("limiter socket path is occupied")
		}
		if err := owned(info, false); err != nil {
			return err
		}
		connection, err := net.DialTimeout("unix", path, time.Second)
		if err == nil {
			connection.Close()
			return errors.New("another limiter control service is running")
		}
		if !staleSocket(err) {
			return errors.New("cannot verify an existing limiter socket")
		}
		if err := os.Remove(path); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return err
	}
	listener.SetUnlinkOnClose(false)
	if err := os.Chmod(path, 0600); err != nil {
		listener.Close()
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		listener.Close()
		return err
	}
	server := &http.Server{Handler: http.HandlerFunc(m.serve), ReadHeaderTimeout: 2 * time.Second,
		ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second, IdleTimeout: 5 * time.Second,
		MaxHeaderBytes: 8192}
	m.server = &controlServer{http: server, listener: listener, socket: info}
	m.stop = make(chan struct{})
	go server.Serve(listener)
	go func() {
		timer := time.NewTicker(time.Second)
		defer timer.Stop()
		for {
			select {
			case now := <-timer.C:
				m.evaluate(now)
			case <-m.stop:
				return
			}
		}
	}()
	return nil
}

func (m *Manager) Close() error {
	if m == nil || m.server == nil {
		return nil
	}
	close(m.stop)
	m.server.http.Close()
	path := filepath.Join(m.directory, "control.sock")
	if info, err := os.Lstat(path); err == nil && os.SameFile(info, m.server.socket) {
		os.Remove(path)
	}
	m.mu.Lock()
	var closeFlows []func()
	for f := range m.flows {
		closeFlows = append(closeFlows, f.close)
	}
	m.mu.Unlock()
	for _, closeFlow := range closeFlows {
		closeFlow()
	}
	m.server = nil
	return nil
}

func (m *Manager) apply(changes update, expected string) error {
	m.mu.Lock()
	if expected != "" && expected != revision(m.document) {
		m.mu.Unlock()
		return errors.New("limiter revision changed; refresh before applying")
	}
	if len(changes.Policies)+len(changes.Removals) > 1000 {
		m.mu.Unlock()
		return errors.New("too many limiter changes")
	}
	replace := make(map[string]bool)
	for _, tag := range changes.Removals {
		if !textOK(tag) || replace[tag] {
			m.mu.Unlock()
			return errors.New("invalid removal")
		}
		replace[tag] = true
	}
	for _, policy := range changes.Policies {
		if err := policy.validate(); err != nil {
			m.mu.Unlock()
			return err
		}
		if replace[policy.InboundTag] {
			m.mu.Unlock()
			return errors.New("duplicate limiter change")
		}
		replace[policy.InboundTag] = true
	}
	document := Document{Version: 1, Inbounds: []Policy{}}
	for _, policy := range m.document.Inbounds {
		if !replace[policy.InboundTag] {
			document.Inbounds = append(document.Inbounds, policy)
		}
	}
	document.Inbounds = append(document.Inbounds, changes.Policies...)
	if err := document.validate(); err != nil {
		m.mu.Unlock()
		return err
	}
	raw, err := json.Marshal(document)
	if err == nil && len(raw) > MaxBytes {
		err = errors.New("limiter policy exceeds 2 MiB")
	}
	if err == nil {
		err = persist(m.directory, raw, m.raw)
	}
	if err != nil {
		m.mu.Unlock()
		return err
	}
	closeFlows := m.install(document, raw)
	m.mu.Unlock()
	for _, closeFlow := range closeFlows {
		closeFlow()
	}
	return nil
}

func (m *Manager) serve(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if r.URL.Path != "/v1/limiter" && r.URL.Path != "/v1/limiter/batch" {
		http.NotFound(w, r)
		return
	}
	if r.Method == "GET" && r.URL.Path == "/v1/limiter" {
		json.NewEncoder(w).Encode(m.snapshot())
		return
	}
	if r.Method != "POST" && r.Method != "DELETE" {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var changes update
	var err error
	if r.Method == "DELETE" {
		changes.Removals = []string{r.URL.Query().Get("inbound_tag")}
	} else {
		raw, readErr := io.ReadAll(io.LimitReader(r.Body, MaxBytes+1))
		err = readErr
		if err == nil {
			if r.URL.Path == "/v1/limiter/batch" {
				err = decode(raw, &changes)
			} else {
				var policy Policy
				err = decode(raw, &policy)
				changes.Policies = []Policy{policy}
			}
		}
	}
	if err == nil {
		err = m.apply(changes, r.URL.Query().Get("expected_revision"))
	}
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]any{"success": false, "error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(m.snapshot())
}
