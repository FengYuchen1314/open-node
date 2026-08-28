// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"context"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

type bucket struct {
	mu      sync.Mutex
	limiter *rate.Limiter
	changed chan struct{}
	value   int64
}

func newBucket() *bucket {
	return &bucket{limiter: rate.NewLimiter(rate.Inf, 65536), changed: make(chan struct{})}
}

func (b *bucket) set(value int64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if value == b.value {
		return
	}
	limit, burst := rate.Inf, 65536
	if value > 0 {
		limit = rate.Limit(value)
		burst = int(max(2048, min(value/10, 65536)))
	}
	b.limiter.SetLimit(limit)
	b.limiter.SetBurst(burst)
	b.value = value
	close(b.changed)
	b.changed = make(chan struct{})
}

func (b *bucket) wait(ctx context.Context, size int) error {
	for size > 0 {
		if err := ctx.Err(); err != nil {
			return err
		}
		b.mu.Lock()
		n := min(size, b.limiter.Burst())
		changed := b.changed
		reservation := b.limiter.ReserveN(time.Now(), n)
		delay := reservation.Delay()
		b.mu.Unlock()
		if delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				reservation.Cancel()
				return ctx.Err()
			case <-changed:
				timer.Stop()
				reservation.Cancel()
				continue
			case <-timer.C:
			}
		}
		size -= n
	}
	return nil
}
