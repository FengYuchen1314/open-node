// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"strings"
)

const MaxBytes = 2 * 1024 * 1024
const maxRate = 1 << 50

type User struct {
	UID            int64  `json:"uid"`
	Email          string `json:"email"`
	SpeedLimit     int64  `json:"speed_limit"`
	DeviceLimit    int    `json:"device_limit"`
	ConnGroup      string `json:"conn_group,omitempty"`
	AutoSpeedRules []Rule `json:"auto_speed_rules,omitempty"`
}

type Rule struct {
	Type             string  `json:"type"`
	ThresholdMbps    float64 `json:"threshold_mbps"`
	SustainedSeconds int     `json:"sustained_seconds"`
	WindowSeconds    int     `json:"window_seconds"`
	BurstCount       int     `json:"burst_count"`
	LimitMbps        float64 `json:"limit_mbps"`
	LimitDuration    int     `json:"limit_duration"`
}

type Policy struct {
	InboundTag     string `json:"inbound_tag"`
	NodeLimit      int64  `json:"node_limit"`
	Users          []User `json:"users"`
	AutoSpeedRules []Rule `json:"auto_speed_rules"`
}

type Document struct {
	Version  int      `json:"version"`
	Inbounds []Policy `json:"inbounds"`
}

func decode(raw []byte, value any) error {
	if len(raw) > MaxBytes {
		return errors.New("limiter configuration exceeds 2 MiB")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(value); err != nil {
		return errors.New("invalid limiter JSON")
	}
	if d.Decode(new(any)) != io.EOF {
		return errors.New("limiter JSON contains trailing data")
	}
	return nil
}

func textOK(value string) bool {
	return value != "" && len(value) <= 255 && strings.TrimSpace(value) == value &&
		!strings.ContainsAny(value, "\x00\r\n")
}

func (p Policy) validate() error {
	if !textOK(p.InboundTag) || p.NodeLimit < 0 || p.NodeLimit > maxRate ||
		len(p.Users) > 1000 || len(p.AutoSpeedRules) > 100 {
		return errors.New("invalid inbound limiter policy")
	}
	seen := make(map[string]bool)
	for _, u := range p.Users {
		if u.UID < 0 || !textOK(u.Email) || seen[u.Email] || u.SpeedLimit < 0 ||
			u.SpeedLimit > maxRate || u.DeviceLimit < 0 || u.DeviceLimit > 1000000 ||
			(u.ConnGroup != "" && !textOK(u.ConnGroup)) {
			return errors.New("invalid or duplicate limiter user")
		}
		seen[u.Email] = true
		if err := validateRules(u.AutoSpeedRules); err != nil {
			return err
		}
	}
	return validateRules(p.AutoSpeedRules)
}

func validateRules(rules []Rule) error {
	if len(rules) > 100 {
		return errors.New("too many automatic speed rules")
	}
	for _, r := range rules {
		if (r.Type != "sustained" && r.Type != "burst") ||
			!positiveMbps(r.ThresholdMbps) || !positiveMbps(r.LimitMbps) ||
			r.SustainedSeconds < 1 || r.SustainedSeconds > 86400 ||
			r.LimitDuration < 1 || r.LimitDuration > 86400 ||
			r.WindowSeconds < 0 || r.WindowSeconds > 86400 || r.BurstCount < 0 || r.BurstCount > 10000 ||
			(r.Type == "burst" && (r.WindowSeconds < r.SustainedSeconds ||
				r.WindowSeconds > 86400 || r.BurstCount < 1 || r.BurstCount > 10000)) {
			return errors.New("invalid automatic speed rule")
		}
	}
	return nil
}

func positiveMbps(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value > 0 &&
		value*125000 >= 1 && value*125000 <= maxRate
}

func (d Document) validate() error {
	if d.Version != 1 || len(d.Inbounds) > 1000 {
		return errors.New("unsupported limiter document")
	}
	seen := make(map[string]bool)
	users := 0
	for _, p := range d.Inbounds {
		if err := p.validate(); err != nil {
			return err
		}
		if seen[p.InboundTag] {
			return errors.New("duplicate limiter inbound")
		}
		seen[p.InboundTag] = true
		users += len(p.Users)
	}
	if users > 20000 {
		return errors.New("too many limiter users")
	}
	return nil
}

func minimum(a, b int64) int64 {
	if a == 0 || (b > 0 && b < a) {
		return b
	}
	return a
}

func group(u User, email string) string {
	if u.ConnGroup != "" {
		return u.ConnGroup
	}
	return email
}
