// SPDX-License-Identifier: MPL-2.0
package nodelimits

import (
	"context"

	"github.com/xtls/xray-core/common"
	"github.com/xtls/xray-core/common/buf"
	"github.com/xtls/xray-core/transport"
)

type limitedWriter struct {
	buf.Writer
	flow *flow
}

func (w *limitedWriter) WriteMultiBuffer(buffers buf.MultiBuffer) error {
	if err := w.flow.state.bucket.wait(w.flow.ctx, int(buffers.Len())); err != nil {
		buf.ReleaseMulti(buffers)
		return err
	}
	w.flow.state.total.Add(int64(buffers.Len()))
	return w.Writer.WriteMultiBuffer(buffers)
}
func (w *limitedWriter) Close() error { return common.Close(w.Writer) }
func (w *limitedWriter) Interrupt()   { common.Interrupt(w.Writer) }

type limitedReader struct {
	buf.Reader
	flow *flow
}

func (r *limitedReader) ReadMultiBuffer() (buf.MultiBuffer, error) {
	buffers, err := r.Reader.ReadMultiBuffer()
	if failure := r.flow.state.bucket.wait(r.flow.ctx, int(buffers.Len())); failure != nil {
		buf.ReleaseMulti(buffers)
		return nil, failure
	}
	r.flow.state.total.Add(int64(buffers.Len()))
	return buffers, err
}
func (r *limitedReader) Interrupt() { common.Interrupt(r.Reader) }

func (m *Manager) Bind(ctx context.Context, inbound, outbound *transport.Link) error {
	closeLinks := func() {
		common.Interrupt(inbound.Reader)
		common.Interrupt(outbound.Reader)
		common.Close(inbound.Writer)
		common.Close(outbound.Writer)
	}
	f, err := m.acquire(ctx, closeLinks)
	if err != nil {
		closeLinks()
		return err
	}
	if f != nil {
		inbound.Writer = &limitedWriter{Writer: inbound.Writer, flow: f}
		outbound.Writer = &limitedWriter{Writer: outbound.Writer, flow: f}
	}
	return nil
}

func (m *Manager) BindLink(ctx context.Context, link *transport.Link) error {
	closeLink := func() { common.Interrupt(link.Reader); common.Close(link.Writer) }
	f, err := m.acquire(ctx, closeLink)
	if err != nil {
		closeLink()
		return err
	}
	if f != nil {
		link.Reader = &limitedReader{Reader: link.Reader, flow: f}
		link.Writer = &limitedWriter{Writer: link.Writer, flow: f}
	}
	return nil
}
