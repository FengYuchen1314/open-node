// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/xtls/xray-core/common/protocol"
	"github.com/xtls/xray-core/common/session"
)

func fixture(t *testing.T) *Manager {
	t.Helper()
	root, err := os.MkdirTemp("", "onl-")
	if err != nil {
		t.Fatal(err)
	}
	m := New(filepath.Join(root, "limits"))
	if err := m.Start(); err != nil {
		os.RemoveAll(root)
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Close(); os.RemoveAll(root) })
	return m
}

func policy() Policy {
	return Policy{InboundTag: "in", Users: []User{
		{Email: "alice", SpeedLimit: 5000, DeviceLimit: 1, ConnGroup: "account"},
		{Email: "alias", DeviceLimit: 1, ConnGroup: "account"},
	}}
}

func applyPolicy(t *testing.T, m *Manager, p Policy) {
	t.Helper()
	if err := m.apply(update{Policies: []Policy{p}}, ""); err != nil {
		t.Fatal(err)
	}
}

func connect(m *Manager, email string, interrupt func()) (*flow, context.CancelFunc, error) {
	ctx, cancel := context.WithCancel(context.Background())
	ctx = session.ContextWithInbound(ctx, &session.Inbound{
		Tag: "in", User: &protocol.MemoryUser{Email: email},
	})
	f, err := m.acquire(ctx, interrupt)
	return f, cancel, err
}

func eventually(t *testing.T, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if check() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("condition did not become true")
}

func TestSharedConnections(t *testing.T) {
	m := fixture(t)
	applyPolicy(t, m, policy())
	f, cancel, err := connect(m, "alice", func() {})
	if err != nil || f == nil {
		t.Fatal(err)
	}
	defer cancel()
	_, rejected, err := connect(m, "alias", func() {})
	rejected()
	if err == nil {
		t.Fatal("alias bypassed its shared connection group")
	}
	if m.snapshot()["connection_rejections"].(map[string]int64)["alias"] != 1 {
		t.Fatal("missing rejection")
	}
	cancel()
	eventually(t, func() bool { return len(m.snapshot()["conn_counts"].(map[string]int)) == 0 })
	_, release, err := connect(m, "alias", func() {})
	defer release()
	if err != nil {
		t.Fatal(err)
	}
}

func TestConcurrentAdmission(t *testing.T) {
	m := fixture(t)
	applyPolicy(t, m, policy())
	results := make(chan *flow, 100)
	for i := 0; i < 100; i++ {
		go func() {
			f, cancel, err := connect(m, "alice", func() {})
			if err != nil {
				cancel()
			}
			results <- f
		}()
	}
	var admitted []*flow
	for i := 0; i < 100; i++ {
		if f := <-results; f != nil {
			admitted = append(admitted, f)
		}
	}
	defer func() {
		for _, f := range admitted {
			f.cancel()
		}
	}()
	if len(admitted) != 1 {
		t.Fatalf("admitted %d instead of 1", len(admitted))
	}
}

func TestHotUpdateAndRemoval(t *testing.T) {
	m := fixture(t)
	p := policy()
	applyPolicy(t, m, p)
	var interrupted atomic.Bool
	f, cancel, err := connect(m, "alice", func() { interrupted.Store(true) })
	defer cancel()
	if err != nil {
		t.Fatal(err)
	}
	bucket := f.state.bucket
	p.Users[0].SpeedLimit = 9000
	p.Users[0].ConnGroup = "new-group"
	applyPolicy(t, m, p)
	if f.state.bucket != bucket || float64(bucket.limiter.Limit()) != 9000 {
		t.Fatal("existing bucket was not updated")
	}
	if m.snapshot()["conn_counts"].(map[string]int)["new-group"] != 1 {
		t.Fatal("connection group was not moved")
	}
	p.Users = p.Users[1:]
	applyPolicy(t, m, p)
	eventually(t, interrupted.Load)
	eventually(t, func() bool { return len(m.snapshot()["conn_counts"].(map[string]int)) == 0 })
}

func TestHotWaitAndCancellation(t *testing.T) {
	b := newBucket()
	b.set(1)
	done := make(chan error, 1)
	go func() { done <- b.wait(context.Background(), 4096) }()
	time.Sleep(30 * time.Millisecond)
	b.set(0)
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("live limit increase did not wake a waiting connection")
	}
	b.set(1)
	ctx, cancel := context.WithCancel(context.Background())
	go func() { done <- b.wait(ctx, 65536) }()
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancellation was ignored")
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled reservation stayed blocked")
	}
}

func TestIdleStateCleanupPreservesEveryActiveFlow(t *testing.T) {
	m := New(t.TempDir())
	first, firstCancel, err := connect(m, "guest", func() {})
	if err != nil {
		t.Fatal(err)
	}
	defer firstCancel()
	second, secondCancel, err := connect(m, "guest", func() {})
	if err != nil {
		t.Fatal(err)
	}
	defer secondCancel()
	now := time.Now()
	firstCancel()
	first.release()
	first.release()
	m.evaluate(now.Add(2 * time.Minute))
	if m.states[key("in", "guest")] != second.state || second.state.active != 1 {
		t.Fatal("idle state was removed while another flow remained active")
	}
	secondCancel()
	second.release()
	m.evaluate(now.Add(4 * time.Minute))
	if len(m.states) != 0 || len(m.flows) != 0 || len(m.counts) != 0 {
		t.Fatal("inactive unconfigured state was retained")
	}
}

func TestAutomaticRules(t *testing.T) {
	for _, kind := range []string{"sustained", "burst"} {
		t.Run(kind, func(t *testing.T) {
			m := fixture(t)
			p := policy()
			p.AutoSpeedRules = []Rule{{Type: kind, ThresholdMbps: .001,
				SustainedSeconds: 1, WindowSeconds: 10, BurstCount: 2,
				LimitMbps: .0008, LimitDuration: 2}}
			applyPolicy(t, m, p)
			f, cancel, err := connect(m, "alice", func() {})
			defer cancel()
			if err != nil {
				t.Fatal(err)
			}
			start := m.lastTick
			sample := func(second int, bytes int64) {
				f.state.total.Add(bytes)
				m.evaluate(start.Add(time.Duration(second) * time.Second))
			}
			sample(1, 10000)
			if kind == "sustained" {
				sample(2, 10000)
			} else {
				sample(2, 0)
				sample(3, 10000)
				sample(4, 0)
			}
			if float64(f.state.bucket.limiter.Limit()) != 100 {
				t.Fatal("automatic cap did not activate")
			}
			applyPolicy(t, m, p)
			if float64(f.state.bucket.limiter.Limit()) != 100 {
				t.Fatal("policy refresh reset active automatic cap")
			}
			sample(8, 0)
			if float64(f.state.bucket.limiter.Limit()) != 5000 {
				t.Fatal("static cap was not restored")
			}
		})
	}
}

func TestPersistenceAndRevision(t *testing.T) {
	m := fixture(t)
	before := m.snapshot()["revision"].(string)
	applyPolicy(t, m, policy())
	if err := m.apply(update{Removals: []string{"in"}}, before); err == nil {
		t.Fatal("stale write accepted")
	}
	m.Close()
	fresh := New(m.directory)
	if err := fresh.Start(); err != nil {
		t.Fatal(err)
	}
	defer fresh.Close()
	if len(fresh.snapshot()["inbounds"].([]Policy)) != 1 {
		t.Fatal("policy lost after restart")
	}
	another := New(m.directory)
	if err := another.Start(); err == nil {
		another.Close()
		t.Fatal("active socket replaced")
	}
	raw := []byte(`{"version":1,"inbounds":[]}`)
	if err := os.WriteFile(filepath.Join(m.directory, "policy.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	if err := fresh.apply(update{Removals: []string{"in"}}, ""); err == nil {
		t.Fatal("independent policy edit overwritten")
	}
}

func TestInvalidPolicies(t *testing.T) {
	for _, raw := range []string{
		`{"version":2,"inbounds":[]}`,
		`{"version":1,"inbounds":[{"inbound_tag":"in","node_limit":-1}]}`,
		`{"version":1,"inbounds":[{"inbound_tag":"in","users":[{"email":"a"},{"email":"a"}]}]}`,
		`{"version":1,"inbounds":[{"inbound_tag":"in","auto_speed_rules":[{"type":"burst"}]}]}`,
		`{"version":1,"unknown":true,"inbounds":[]}`,
		`{"version":1,"inbounds":[]}{}`,
	} {
		var document Document
		err := decode([]byte(raw), &document)
		if err == nil {
			err = document.validate()
		}
		if err == nil {
			t.Fatalf("accepted invalid policy: %s", raw)
		}
	}
}

func TestPrivateFiles(t *testing.T) {
	m := fixture(t)
	applyPolicy(t, m, policy())
	path := filepath.Join(m.directory, "policy.json")
	var doc Document
	raw, err := readPolicy(path)
	if err != nil || json.Unmarshal(raw, &doc) != nil {
		t.Fatal(err)
	}
	if err := os.Link(path, path+".link"); err != nil {
		t.Fatal(err)
	}
	if _, err := readPolicy(path); err == nil {
		t.Fatal("hard-linked policy accepted")
	}
	os.Remove(path + ".link")
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := readPolicy(path); err == nil {
		t.Fatal("public policy accepted")
	}
}
