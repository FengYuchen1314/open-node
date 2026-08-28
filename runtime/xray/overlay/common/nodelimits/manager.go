// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"sync"
	"sync/atomic"
	"time"

	"github.com/xtls/xray-core/common/session"
)

type ruleState struct {
	since  time.Time
	events []time.Time
}

type userState struct {
	tag, email      string
	bucket          *bucket
	total           atomic.Int64
	previous, speed int64
	lastSeen        time.Time
	until           time.Time
	autoRate        int64
	rules           []ruleState
	configuration   []Rule
	active          int
}

type flow struct {
	manager   *Manager
	state     *userState
	group     string
	known     bool
	ctx       context.Context
	cancel    context.CancelFunc
	interrupt func()
	once      sync.Once
}

type Manager struct {
	mu        sync.Mutex
	directory string
	document  Document
	raw       []byte
	policies  map[string]Policy
	users     map[string]User
	states    map[string]*userState
	flows     map[*flow]bool
	counts    map[string]int
	caps      map[string]int64
	rejected  map[string]int64
	lastTick  time.Time
	stop      chan struct{}
	server    *controlServer
}

func New(directory string) *Manager {
	if directory == "" {
		return nil
	}
	return &Manager{
		directory: directory, document: Document{Version: 1, Inbounds: []Policy{}},
		policies: map[string]Policy{}, users: map[string]User{}, states: map[string]*userState{},
		flows: map[*flow]bool{}, counts: map[string]int{}, caps: map[string]int64{},
		rejected: map[string]int64{}, lastTick: time.Now(),
	}
}

func FromEnvironment() *Manager    { return New(os.Getenv("OPEN_NODE_LIMITER_DIR")) }
func key(tag, email string) string { return tag + "\x00" + email }

func (m *Manager) install(document Document, raw []byte) []func() {
	m.document, m.raw = document, raw
	m.policies, m.users, m.caps = map[string]Policy{}, map[string]User{}, map[string]int64{}
	for _, p := range document.Inbounds {
		m.policies[p.InboundTag] = p
		for _, u := range p.Users {
			m.users[key(p.InboundTag, u.Email)] = u
			g := group(u, u.Email)
			m.caps[g] = minimum(m.caps[g], int64(u.DeviceLimit))
		}
	}
	for _, state := range m.states {
		configuration := m.rulesFor(state.tag, state.email)
		if !reflect.DeepEqual(state.configuration, configuration) {
			state.configuration = configuration
			state.rules = make([]ruleState, len(configuration))
			state.until, state.autoRate = time.Time{}, 0
		}
		m.refreshRate(state)
	}
	m.counts = map[string]int{}
	var closeFlows []func()
	for f := range m.flows {
		u, known := m.users[key(f.state.tag, f.state.email)]
		_, policyExists := m.policies[f.state.tag]
		if f.known && !known && policyExists {
			closeFlows = append(closeFlows, f.close)
		}
		f.group, f.known = group(u, f.state.email), known
		m.counts[f.group]++
	}
	return closeFlows
}

func (m *Manager) refreshRate(state *userState) {
	value := minimum(m.policies[state.tag].NodeLimit, m.users[key(state.tag, state.email)].SpeedLimit)
	state.bucket.set(minimum(value, state.autoRate))
}

func (m *Manager) rulesFor(tag, email string) []Rule {
	inbound := m.policies[tag].AutoSpeedRules
	user := m.users[key(tag, email)].AutoSpeedRules
	if len(user) == 0 {
		return inbound
	}
	rules := make([]Rule, 0, len(inbound)+len(user))
	return append(append(rules, inbound...), user...)
}

func (m *Manager) acquire(ctx context.Context, interrupt func()) (*flow, error) {
	if m == nil {
		return nil, nil
	}
	inbound := session.InboundFromContext(ctx)
	if inbound == nil || inbound.User == nil || inbound.User.Email == "" {
		return nil, nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	tag, email := inbound.Tag, inbound.User.Email
	u, known := m.users[key(tag, email)]
	g := group(u, email)
	if limit := m.caps[g]; limit > 0 && int64(m.counts[g]) >= limit {
		m.rejected[email]++
		return nil, errors.New("concurrent connection limit reached")
	}
	state := m.states[key(tag, email)]
	if state == nil {
		configuration := m.rulesFor(tag, email)
		state = &userState{tag: tag, email: email, bucket: newBucket(),
			rules: make([]ruleState, len(configuration)), configuration: configuration, lastSeen: time.Now()}
		m.states[key(tag, email)] = state
		m.refreshRate(state)
	}
	child, cancel := context.WithCancel(ctx)
	f := &flow{manager: m, state: state, group: g, known: known,
		ctx: child, cancel: cancel, interrupt: interrupt}
	m.flows[f] = true
	state.active++
	m.counts[g]++
	// Keep all authenticated paths in user space so future hot limits also affect Vision.
	inbound.CanSpliceCopy = 3
	context.AfterFunc(child, f.release)
	return f, nil
}

func (f *flow) close() {
	f.cancel()
	f.interrupt()
}

func (f *flow) release() {
	f.once.Do(func() {
		m := f.manager
		m.mu.Lock()
		defer m.mu.Unlock()
		delete(m.flows, f)
		f.state.active--
		m.counts[f.group]--
		if m.counts[f.group] <= 0 {
			delete(m.counts, f.group)
		}
		f.state.lastSeen = time.Now()
	})
}

func (m *Manager) evaluate(now time.Time) {
	m.mu.Lock()
	defer m.mu.Unlock()
	elapsed := now.Sub(m.lastTick).Seconds()
	if elapsed <= 0 {
		return
	}
	m.lastTick = now
	for k, state := range m.states {
		total := state.total.Load()
		state.speed = max(0, int64(float64(total-state.previous)/elapsed))
		state.previous = total
		if state.speed > 0 {
			state.lastSeen = now
		}
		if !state.until.IsZero() && !now.Before(state.until) {
			state.until, state.autoRate = time.Time{}, 0
			m.refreshRate(state)
		}
		if now.Before(state.until) {
			continue
		}
		for i, rule := range state.configuration {
			rs := &state.rules[i]
			exceeds := float64(state.speed) > rule.ThresholdMbps*125000
			if exceeds && rs.since.IsZero() {
				rs.since = now
			}
			trigger := false
			if rule.Type == "sustained" {
				trigger = exceeds && now.Sub(rs.since) >= time.Duration(rule.SustainedSeconds)*time.Second
			} else {
				cutoff := now.Add(-time.Duration(rule.WindowSeconds) * time.Second)
				kept := rs.events[:0]
				for _, event := range rs.events {
					if event.After(cutoff) {
						kept = append(kept, event)
					}
				}
				rs.events = kept
				if !exceeds && !rs.since.IsZero() &&
					now.Sub(rs.since) >= time.Duration(rule.SustainedSeconds)*time.Second {
					rs.events = append(rs.events, now)
				}
				trigger = len(rs.events) >= rule.BurstCount
			}
			if !exceeds {
				rs.since = time.Time{}
			}
			if trigger {
				state.autoRate = int64(rule.LimitMbps * 125000)
				state.until = now.Add(time.Duration(rule.LimitDuration) * time.Second)
				state.rules = make([]ruleState, len(state.rules))
				m.refreshRate(state)
				break
			}
		}
		if _, configured := m.users[k]; !configured && state.active == 0 &&
			now.Sub(state.lastSeen) > time.Minute {
			delete(m.states, k)
		}
	}
}

func (m *Manager) snapshot() map[string]any {
	m.mu.Lock()
	defer m.mu.Unlock()
	speeds, automatic := map[string]int64{}, map[string]any{}
	for _, state := range m.states {
		speeds[state.email] += state.speed
		if !state.until.IsZero() {
			automatic[key(state.tag, state.email)] = map[string]any{
				"inbound_tag": state.tag, "email": state.email,
				"bytes_per_second": state.autoRate, "until": state.until.UTC().Format(time.RFC3339Nano),
			}
		}
	}
	counts, rejected := map[string]int{}, map[string]int64{}
	for k, v := range m.counts {
		counts[k] = v
	}
	for k, v := range m.rejected {
		rejected[k] = v
	}
	return map[string]any{"success": true, "protocol_version": 1, "pid": os.Getpid(),
		"revision": revision(m.document), "inbounds": m.document.Inbounds,
		"conn_counts": counts, "user_speeds": speeds, "connection_rejections": rejected,
		"automatic_limits": automatic}
}

func revision(document Document) string {
	raw, _ := json.Marshal(document)
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}
