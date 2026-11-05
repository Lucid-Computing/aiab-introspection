package main

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"github.com/tinfoilsh/tinfoil-go/verifier/client"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"time"
)

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type fakeVerifier struct {
	d        *verificationDocument
	sends    int
	verifies int
	status   int
	body     string
}

func good() *fakeVerifier {
	d := &verificationDocument{ConfigRepo: repo, EnclaveHost: host, ReleaseDigest: digest, TLSPublicKey: key,
		SecurityVerified: true, FreshnessExpiresAt: time.Now().Add(time.Hour), Steps: map[string]map[string]string{}}
	for _, name := range []string{"fetchDigest", "verifyCode", "verifyEnclave", "compareMeasurements"} {
		d.Steps[name] = map[string]string{"status": "success"}
	}
	return &fakeVerifier{d: d, status: 200, body: `{"count":10}`}
}
func (f *fakeVerifier) Verify() (*verificationDocument, error) { f.verifies++; return f.d, nil }
func (f *fakeVerifier) HTTPClient() (*http.Client, error) {
	return &http.Client{Transport: roundTrip(func(r *http.Request) (*http.Response, error) {
		f.sends++
		if r.URL.String() != "https://"+host+"/tokenize" || r.Header.Get("Authorization") != "Bearer test-only" || r.GetBody != nil {
			panic("unsafe request")
		}
		return &http.Response{StatusCode: f.status, Header: http.Header{"Location": []string{"https://attacker.invalid/"}, "Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(f.body)), Request: r}, nil
	})}, nil
}
func tokenize() frame {
	return frame{Op: "request", Path: "/tokenize", Body: json.RawMessage(`{"model":"deepseek-v4-1-flash"}`)}
}

func TestIdentityBeforeEveryBody(t *testing.T) {
	for _, which := range []string{"repo", "release", "key", "host", "step", "verified", "missing", "expired"} {
		t.Run(which, func(t *testing.T) {
			v := good()
			b := broker{secure: v, credential: "test-only"}
			if _, err := b.request(tokenize()); err != nil {
				t.Fatal(err)
			}
			switch which {
			case "repo":
				v.d.ConfigRepo = "tinfoilsh/other"
			case "release":
				v.d.ReleaseDigest = "other"
			case "key":
				v.d.TLSPublicKey = "rotated"
			case "host":
				v.d.EnclaveHost = "other.tinfoil.dev"
			case "step":
				v.d.Steps["verifyCode"]["status"] = "skipped"
			case "verified":
				v.d.SecurityVerified = false
			case "expired":
				v.d.FreshnessExpiresAt = time.Now().Add(-time.Second)
			case "missing":
				v.d = nil
			}
			if _, err := b.request(tokenize()); err == nil || v.sends != 1 || v.verifies != 2 {
				t.Fatal("sensitive body sent after changed identity")
			}
		})
	}
}

func TestRedirectAndMalformedOrOversizeResponse(t *testing.T) {
	for _, tc := range []struct {
		status int
		body   string
	}{{302, `{}`}, {200, "data: {}\n\n"}, {200, `{"count":`}, {200, `[]`}, {200, strings.Repeat("x", frameLimit+1)}} {
		v := good()
		v.status = tc.status
		v.body = tc.body
		b := broker{secure: v, credential: "test-only"}
		if _, err := b.request(tokenize()); err == nil || v.sends != 1 {
			t.Fatal("redirect/invalid response accepted or replayed")
		}
	}
}

func TestNoAuthBodyEcho(t *testing.T) {
	v := good()
	v.status = 401
	v.body = "test-only"
	b := broker{secure: v, credential: "test-only"}
	r, err := b.request(tokenize())
	if err != nil || r.Status != 401 || string(r.Body) != "{}" {
		t.Fatal("credentialed error body escaped")
	}
}

func TestPathsModelsStreamingAndTokenLimits(t *testing.T) {
	for _, f := range []frame{
		{Path: "https://attacker.invalid", Body: json.RawMessage(`{"model":"deepseek-v4-1-flash"}`)},
		{Path: "/tokenize", Body: json.RawMessage(`{"model":"other"}`)},
		{Path: "/v1/chat/completions", Body: json.RawMessage(`{"model":"deepseek-v4-1-flash","stream":true,"max_tokens":1}`)},
		{Path: "/v1/chat/completions", Body: json.RawMessage(`{"model":"deepseek-v4-1-flash","stream":false,"max_tokens":0}`)},
		{Path: "/v1/chat/completions", Body: json.RawMessage(`{"model":"deepseek-v4-1-flash","stream":false,"max_tokens":1048577}`)},
	} {
		v := good()
		b := broker{secure: v, credential: "test-only"}
		if _, err := b.request(f); err == nil || v.sends != 0 {
			t.Fatal("invalid sensitive request sent")
		}
	}
}

func TestMalformedProtocolClosesWithoutEcho(t *testing.T) {
	for _, s := range []string{"not json\n", `{"op":"init","credential":"test-only","unknown":true}` + "\n", strings.Repeat("x", frameLimit+1) + "\n"} {
		var out bytes.Buffer
		run(strings.NewReader(s), &out)
		if out.String() != "{\"ok\":false,\"error\":\"verified_transport_failed\"}\n" {
			t.Fatal("invalid input not closed safely")
		}
	}
	for _, s := range []string{`{} {}`, `{"unexpected":true}`, `data: {}`} {
		if _, err := decodeFrame([]byte(s)); err == nil {
			t.Fatal("malformed frame accepted")
		}
	}
}

// Exercise the real SDK TLS binding: even a normally trusted server must not
// receive an HTTP body until its certificate key matches the attested key.
func TestSDKRejectsKeyBeforeSensitiveBody(t *testing.T) {
	sends := 0
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sends++
		io.Copy(io.Discard, r.Body)
		w.Write([]byte(`{}`))
	}))
	defer server.Close()
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	original := http.DefaultTransport
	transport := original.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{RootCAs: roots}
	http.DefaultTransport = transport
	defer func() { http.DefaultTransport = original; transport.CloseIdleConnections() }()
	wrong := &client.TLSBoundRoundTripper{ExpectedPublicKey: strings.Repeat("0", 64)}
	defer wrong.CloseIdleConnections()
	hc := &http.Client{Transport: wrong}
	if _, err := hc.Post(server.URL, "application/json", strings.NewReader(`{"synthetic":"private"}`)); err == nil || sends != 0 {
		t.Fatal("HTTP body reached a trusted server with the wrong attested key")
	}
	fingerprint, err := client.CertPubkeyFP(server.Certificate())
	if err != nil {
		t.Fatal(err)
	}
	right := &client.TLSBoundRoundTripper{ExpectedPublicKey: fingerprint}
	defer right.CloseIdleConnections()
	hc.Transport = right
	response, err := hc.Post(server.URL, "application/json", strings.NewReader(`{"synthetic":"private"}`))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if sends != 1 {
		t.Fatal("correct key failed positive control")
	}
}
